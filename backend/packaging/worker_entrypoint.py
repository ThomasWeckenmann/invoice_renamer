"""PyInstaller entrypoint that builds into the Tauri sidecar executable."""

import ctypes
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Mapping

# Emitted before the application imports below, so a startup measurement can
# tell the cost of getting to Python at all (which for a onefile build includes
# unpacking the bundle to a temporary directory) apart from the worker's own
# imports and socket bind. Opt-in, so a normal launch prints nothing extra.
if os.environ.get("INVOICE_RENAMER_STARTUP_TRACE"):
    print(f"INVOICE_RENAMER_TRACE python_entry {time.time():.6f}", flush=True)

_PARENT_PID_ENV_VAR = "INVOICE_RENAMER_PARENT_PID"
_PARENT_POLL_INTERVAL_SECONDS = 1.0


def _resolve_expected_parent_pid(
    *,
    environ: Mapping[str, str] = os.environ,
    getppid_fn: Callable[[], int] = os.getppid,
) -> int:
    """The pid this worker must not outlive.

    `worker.rs` captures its own pid before it ever spawns us and passes it
    via `INVOICE_RENAMER_PARENT_PID` - the only way to know the *intended*
    parent even if we've already been reparented (to init/launchd) by the
    time this code runs, which our own `getppid()` could never tell us by
    itself. Falling back to `getppid()` only covers a worker started by hand
    outside the app, where there is no launcher pid to be given and whatever
    started it is, by definition, still the real parent.
    """
    raw = environ.get(_PARENT_PID_ENV_VAR)
    if raw is None:
        return getppid_fn()
    try:
        return int(raw)
    except ValueError:
        return getppid_fn()


def _prctl_set_pdeathsig(sig: int) -> int:
    """Raw `prctl(PR_SET_PDEATHSIG, sig)` call; Linux-only, via ctypes since
    the stdlib has no binding for it. `None` loads the process's own global
    symbol namespace, which always includes libc since every CPython build
    this app ships on is dynamically linked against it.
    """
    PR_SET_PDEATHSIG = 1
    libc = ctypes.CDLL(None, use_errno=True)
    result: int = libc.prctl(PR_SET_PDEATHSIG, sig, 0, 0, 0)
    return result


def _install_linux_pdeathsig(
    *,
    expected_parent_pid: int,
    getppid_fn: Callable[[], int] = os.getppid,
    prctl_fn: Callable[[int], int] = _prctl_set_pdeathsig,
    exit_fn: Callable[[int], None] = os._exit,
) -> None:
    """Asks the kernel to SIGTERM this process the instant its parent dies,
    so an abrupt kill of the Tauri app (force-quit, crash, OOM) can't orphan
    this worker the way the graceful `RunEvent::Exit` shutdown already
    handles.

    `PR_SET_PDEATHSIG` arms against whichever process is our *actual*
    current parent at the moment of this call - if that's already someone
    other than `expected_parent_pid` (we were reparented before this code
    even ran, e.g. because the real parent died while we were still
    starting up), the kernel just armed the signal against the wrong,
    likely-immortal process and it will never fire. The check below is what
    catches that case, by comparing against the pid we were actually told
    to watch rather than one we'd otherwise have to discover ourselves after
    it's already too late. It also covers the kernel's own documented race
    (the parent dying between the `prctl` call and this check), though that
    window is now only a couple of instructions wide.
    """
    if prctl_fn(int(signal.SIGTERM)) != 0:
        return
    if getppid_fn() != expected_parent_pid:
        exit_fn(1)


def _watch_parent_pid(
    *,
    expected_parent_pid: int,
    poll_interval: float = _PARENT_POLL_INTERVAL_SECONDS,
    getppid_fn: Callable[[], int] = os.getppid,
    sleep_fn: Callable[[float], None] = time.sleep,
    exit_fn: Callable[[int], None] = os._exit,
) -> None:
    """Polls for a parent-pid change and self-exits when it happens.

    macOS has no `PR_SET_PDEATHSIG` equivalent, so this is the fallback used
    there. Comparing against `expected_parent_pid` - the pid the launcher
    told us to watch - rather than whatever `getppid()` happens to return on
    entry means a parent that's already gone before this loop starts (e.g.
    it died mid-startup, before this thread was even spawned) is caught on
    the very first check instead of being silently adopted as the new
    baseline.
    """
    while getppid_fn() == expected_parent_pid:
        sleep_fn(poll_interval)
    exit_fn(1)


def install_parent_death_handler(
    *,
    expected_parent_pid: int | None = None,
    platform: str = sys.platform,
    environ: Mapping[str, str] = os.environ,
    getppid_fn: Callable[[], int] = os.getppid,
    prctl_fn: Callable[[int], int] = _prctl_set_pdeathsig,
    sleep_fn: Callable[[float], None] = time.sleep,
    exit_fn: Callable[[int], None] = os._exit,
) -> None:
    """Makes sure this worker cannot outlive an abruptly-killed parent app.

    Graceful shutdown (`RunEvent::Exit` in `worker.rs`) already signals the
    whole sidecar process group, but that path never runs if the Tauri app
    itself is force-quit, crashes, or gets OOM-killed - and the sidecar is
    placed in its own detached session (`setsid()`, also in `worker.rs`), so
    nothing else would notice its parent is gone. Call this as early as
    possible (before the heavy application import that follows it in this
    file), so a parent that dies during that import is still caught quickly
    instead of only once `main()` has already started.
    """
    if expected_parent_pid is None:
        expected_parent_pid = _resolve_expected_parent_pid(environ=environ, getppid_fn=getppid_fn)

    if platform == "linux":
        _install_linux_pdeathsig(
            expected_parent_pid=expected_parent_pid,
            getppid_fn=getppid_fn,
            prctl_fn=prctl_fn,
            exit_fn=exit_fn,
        )
        return
    threading.Thread(
        target=_watch_parent_pid,
        kwargs={
            "expected_parent_pid": expected_parent_pid,
            "getppid_fn": getppid_fn,
            "sleep_fn": sleep_fn,
            "exit_fn": exit_fn,
        },
        daemon=True,
    ).start()


if __name__ == "__main__":
    install_parent_death_handler()

    from invoice_renamer.api.server import main

    main()
