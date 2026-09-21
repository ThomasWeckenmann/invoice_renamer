"""Tests for the sidecar's parent-death detection (Linux prctl, macOS poll)."""

import importlib.util
import signal
import threading
from pathlib import Path
from types import ModuleType

_MODULE_PATH = Path(__file__).resolve().parents[2] / "packaging" / "worker_entrypoint.py"


def _load_worker_entrypoint() -> ModuleType:
    # Loaded by path rather than imported normally: this script lives outside
    # the `invoice_renamer` package (it's the PyInstaller entry script, not
    # library code) and isn't on any test import path. Its heavy application
    # import is gated behind `__name__ == "__main__"`, so loading it this way
    # never pulls in torch/fastapi/etc.
    spec = importlib.util.spec_from_file_location("worker_entrypoint_under_test", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker_entrypoint = _load_worker_entrypoint()


def test_resolve_expected_parent_pid_uses_the_env_var_when_present() -> None:
    pid = worker_entrypoint._resolve_expected_parent_pid(
        environ={"INVOICE_RENAMER_PARENT_PID": "4321"},
        getppid_fn=lambda: 1,
    )

    assert pid == 4321


def test_resolve_expected_parent_pid_falls_back_to_getppid_without_the_env_var() -> None:
    pid = worker_entrypoint._resolve_expected_parent_pid(environ={}, getppid_fn=lambda: 777)

    assert pid == 777


def test_resolve_expected_parent_pid_falls_back_on_a_garbled_env_var() -> None:
    pid = worker_entrypoint._resolve_expected_parent_pid(
        environ={"INVOICE_RENAMER_PARENT_PID": "not-a-pid"},
        getppid_fn=lambda: 777,
    )

    assert pid == 777


def test_watch_parent_pid_exits_once_ppid_changes() -> None:
    ppids = iter([100, 100, 200])
    sleeps: list[float] = []
    exits: list[int] = []

    worker_entrypoint._watch_parent_pid(
        expected_parent_pid=100,
        poll_interval=0.01,
        getppid_fn=lambda: next(ppids),
        sleep_fn=sleeps.append,
        exit_fn=exits.append,
    )

    assert sleeps == [0.01, 0.01]
    assert exits == [1]


def test_watch_parent_pid_exits_immediately_if_already_mismatched() -> None:
    # Regression coverage: the parent died before this loop ever started
    # (e.g. during the worker's own startup imports), so the very first
    # check must catch it rather than adopting the new ppid as a baseline.
    sleeps: list[float] = []
    exits: list[int] = []

    worker_entrypoint._watch_parent_pid(
        expected_parent_pid=100,
        poll_interval=0.01,
        getppid_fn=lambda: 999,
        sleep_fn=sleeps.append,
        exit_fn=exits.append,
    )

    assert sleeps == []
    assert exits == [1]


def test_install_linux_pdeathsig_does_not_exit_on_the_happy_path() -> None:
    prctl_calls: list[int] = []
    exits: list[int] = []

    worker_entrypoint._install_linux_pdeathsig(
        expected_parent_pid=100,
        getppid_fn=lambda: 100,
        prctl_fn=lambda sig: prctl_calls.append(sig) or 0,
        exit_fn=exits.append,
    )

    assert prctl_calls == [signal.SIGTERM]
    assert exits == []


def test_install_linux_pdeathsig_skips_the_race_check_when_prctl_fails() -> None:
    exits: list[int] = []

    worker_entrypoint._install_linux_pdeathsig(
        expected_parent_pid=100,
        getppid_fn=lambda: 100,
        prctl_fn=lambda sig: 1,
        exit_fn=exits.append,
    )

    assert exits == []


def test_install_linux_pdeathsig_exits_if_already_reparented() -> None:
    # Regression coverage: getppid() already reflects a reparent (to init)
    # that happened before this function ever ran, so the check must compare
    # against the pid we were told to expect, not one derived from getppid()
    # itself.
    exits: list[int] = []

    worker_entrypoint._install_linux_pdeathsig(
        expected_parent_pid=100,
        getppid_fn=lambda: 999,
        prctl_fn=lambda sig: 0,
        exit_fn=exits.append,
    )

    assert exits == [1]


def test_install_parent_death_handler_uses_prctl_on_linux() -> None:
    prctl_calls: list[int] = []

    def fail(code: int) -> None:
        raise AssertionError("must not exit when the parent is unchanged")

    worker_entrypoint.install_parent_death_handler(
        expected_parent_pid=100,
        platform="linux",
        getppid_fn=lambda: 100,
        prctl_fn=lambda sig: prctl_calls.append(sig) or 0,
        exit_fn=fail,
    )

    assert prctl_calls == [signal.SIGTERM]


def test_install_parent_death_handler_resolves_the_pid_from_the_environment() -> None:
    # End-to-end regression for the reported bug: the real parent (pid 100)
    # already died and we've been reparented (getppid() now reports 999)
    # before install_parent_death_handler ever runs - it must still catch
    # this using the env-supplied expected pid, not a self-discovered one.
    exits: list[int] = []

    worker_entrypoint.install_parent_death_handler(
        platform="linux",
        environ={"INVOICE_RENAMER_PARENT_PID": "100"},
        getppid_fn=lambda: 999,
        prctl_fn=lambda sig: 0,
        exit_fn=exits.append,
    )

    assert exits == [1]


def test_install_parent_death_handler_polls_in_a_background_thread_elsewhere() -> None:
    ppids = iter([100, 100, 200])
    exited = threading.Event()
    exit_codes: list[int] = []

    def exit_fn(code: int) -> None:
        exit_codes.append(code)
        exited.set()

    worker_entrypoint.install_parent_death_handler(
        expected_parent_pid=100,
        platform="darwin",
        getppid_fn=lambda: next(ppids),
        sleep_fn=lambda _: None,
        exit_fn=exit_fn,
    )

    assert exited.wait(timeout=2.0)
    assert exit_codes == [1]
