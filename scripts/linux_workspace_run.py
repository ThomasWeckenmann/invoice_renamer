"""Run a mirror command and stop its process group before releasing the inherited lock."""

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    interrupted = 0

    def record_signal(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = interrupted or signum

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, record_signal)

    # Only this supervisor owns the lock; descendants cannot keep it forever.
    # A separate session lets us signal children without signalling our caller.
    try:
        process = subprocess.Popen(sys.argv[1:], start_new_session=True)
    except OSError as error:
        print(f"error: cannot run workspace command: {error}", file=sys.stderr)
        return 127

    def signal_group(signum: int) -> bool:
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            return False
        return True

    try:
        while not interrupted:
            try:
                process.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                pass
    finally:
        # Clean up descendants even if their immediate parent exited first.
        # Escalate after a short grace period so ignored signals cannot hang us.
        if signal_group(interrupted or signal.SIGTERM):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                process.poll()
                if not signal_group(0):
                    break
                time.sleep(0.05)
            signal_group(signal.SIGKILL)
        process.wait()

    if interrupted:
        return 128 + interrupted
    return process.returncode if process.returncode >= 0 else 128 - process.returncode


if __name__ == "__main__":
    sys.exit(main())
