#!/usr/bin/env python3
"""Times a built worker executable from process spawn to its readiness marker.

Point it at a onefile and a onedir build of the same revision to see how much of
startup is the PyInstaller bootloader rather than the worker's own imports.
"""

from __future__ import annotations

import argparse
import os
import platform
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

READY_MARKER = "INVOICE_RENAMER_WORKER_READY"
TRACE_PREFIX = "INVOICE_RENAMER_TRACE "
READY_TIMEOUT = 60.0
TERMINATE_GRACE = 5.0
GROUP_EXIT_TIMEOUT = 2.0
TAIL_LINES = 15


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def _tree_bytes(root: Path) -> int:
    if root.is_file():
        return root.stat().st_size
    total = 0
    for entry in root.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            total += entry.stat().st_size
    return total


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _signal_group(proc: subprocess.Popen[str], sig: int) -> None:
    """Signals the worker's whole process group.

    The worker is spawned into a session of its own, so anything it forked
    shares its group and would otherwise survive a plain kill of the child we
    hold and keep its port bound into the next run.
    """
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _group_is_empty(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _terminate(proc: subprocess.Popen[str]) -> None:
    """Tears the worker's whole process group down, as the desktop shell does."""
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=TERMINATE_GRACE)
    except subprocess.TimeoutExpired:
        pass

    # Sent unconditionally rather than only when the graceful signal failed: a
    # worker can fork a child in the window before SIGTERM arrives, and that
    # child shares the group but never saw the signal. Returning early on a
    # clean parent exit leaves it running into the next run.
    _signal_group(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=TERMINATE_GRACE)
    except subprocess.TimeoutExpired:
        pass

    deadline = time.time() + GROUP_EXIT_TIMEOUT
    while not _group_is_empty(proc.pid):
        if time.time() >= deadline:
            print(
                f"warning: worker process group {proc.pid} still has members",
                file=sys.stderr,
            )
            return
        time.sleep(0.01)


class MeasurementError(RuntimeError):
    pass


def _measure_once(exe: Path) -> tuple[float, float | None]:
    """Returns seconds from spawn to readiness, and to the Python entry marker."""
    env = dict(os.environ)
    env["INVOICE_RENAMER_PORT"] = str(_free_port())
    # Never read during startup, only per request, but production always sets
    # it - a throwaway value keeps this closer to a real launch.
    env["INVOICE_RENAMER_SESSION_TOKEN"] = "startup-measurement"
    env["INVOICE_RENAMER_STARTUP_TRACE"] = "1"

    spawned = time.time()
    proc = subprocess.Popen(
        [str(exe)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    assert proc.stdout is not None

    # A blocking read cannot honour a deadline on its own, and polling the
    # pipe's file descriptor instead would miss lines already sitting in
    # Python's own buffer - a worker that prints its startup output in one
    # burst fills that buffer in a single read, leaving the descriptor quiet
    # while the marker goes unseen. Killing the worker from a timer closes the
    # pipe instead, which ends the loop below at EOF.
    timed_out = threading.Event()

    def give_up() -> None:
        timed_out.set()
        _signal_group(proc, signal.SIGKILL)

    watchdog = threading.Timer(READY_TIMEOUT, give_up)
    watchdog.start()

    python_entry: float | None = None
    ready: float | None = None
    tail: list[str] = []
    try:
        for raw_line in proc.stdout:
            line = raw_line.strip()
            tail.append(line)
            del tail[:-TAIL_LINES]

            if line == READY_MARKER:
                ready = time.time()
                break
            if line.startswith(TRACE_PREFIX):
                phase, _, stamp = line[len(TRACE_PREFIX) :].partition(" ")
                if phase == "python_entry":
                    python_entry = float(stamp) - spawned
    finally:
        watchdog.cancel()
        _terminate(proc)

    if ready is None:
        raise MeasurementError(
            f"no readiness marker within {READY_TIMEOUT:.0f}s"
            if timed_out.is_set()
            else "worker output closed before readiness",
            tail,
        )

    return ready - spawned, python_entry


def _git_revision(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "describe", "--always", "--dirty"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _layout(exe: Path) -> str:
    if (exe.parent / "_internal").is_dir() or exe.parent.name == exe.name:
        return "onedir"
    return "onefile"


def _format_seconds(value: float | None) -> str:
    return "      -" if value is None else f"{value:7.3f}s"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path, help="path to the built worker")
    parser.add_argument("-n", "--runs", type=int, default=5, help="launches to time (default: 5)")
    args = parser.parse_args()

    exe: Path = args.executable.expanduser().resolve()
    if not os.access(exe, os.X_OK) or not exe.is_file():
        parser.error(f"not an executable file: {exe}")
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    repo_root = Path(__file__).resolve().parent.parent
    mac_version = platform.mac_ver()[0]

    print("worker startup measurement")
    print(f"  revision    {_git_revision(repo_root)}")
    print(
        f"  platform    {platform.system()} {platform.machine()}"
        + (f" / macOS {mac_version}" if mac_version else "")
    )
    print(f"  executable  {exe}")
    print(f"  layout      {_layout(exe)} (inferred from the build directory)")
    print(f"  exe size    {_human_bytes(_tree_bytes(exe))}")
    print(f"  tree size   {_human_bytes(_tree_bytes(exe.parent))}  ({exe.parent})")
    print()
    print("  run      total    to python   python->ready")

    totals: list[float] = []
    entries: list[float | None] = []
    for run in range(1, args.runs + 1):
        # Emitted before the launch, not after it, so a slow or wedged startup
        # shows which run is in flight instead of an unexplained pause.
        print(f"  {run:>3}  ", end="", flush=True)
        try:
            total, python_entry = _measure_once(exe)
        except MeasurementError as err:
            print("failed")
            # Keeps the report above the error when the output is piped to a
            # file, where stdout is block-buffered and stderr is not.
            sys.stdout.flush()
            print(f"\nrun {run} failed: {err.args[0]}", file=sys.stderr)
            for line in err.args[1]:
                print(f"  | {line}", file=sys.stderr)
            return 1
        totals.append(total)
        entries.append(python_entry)
        rest = None if python_entry is None else total - python_entry
        print(
            f"{_format_seconds(total)}  {_format_seconds(python_entry)}"
            f"      {_format_seconds(rest)}"
        )

    def summarize(label: str, sample: slice) -> None:
        picked_totals = totals[sample]
        if not picked_totals:
            return
        total_median = statistics.median(picked_totals)
        # Each run's own two phases are differenced before anything is taken
        # across runs. Subtracting one column's median from another's describes
        # no run that actually happened, and diverges as soon as the slowest
        # total is not also the slowest in both phases.
        paired = [
            (entry, total - entry)
            for total, entry in zip(picked_totals, entries[sample])
            if entry is not None
        ]
        entry_median = statistics.median([entry for entry, _ in paired]) if paired else None
        rest = statistics.median([rest for _, rest in paired]) if paired else None
        print(
            f"  {label:<14} total {_format_seconds(total_median)}"
            f"   to python {_format_seconds(entry_median)}"
            f"   python->ready {_format_seconds(rest)}"
        )

    print()
    # Reported apart from the rest: the first launch after a build pays cold
    # page-cache and macOS first-run validation costs that later launches skip.
    summarize("first launch", slice(0, 1))
    if args.runs > 1:
        summarize(f"median of 2-{args.runs}", slice(1, None))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
