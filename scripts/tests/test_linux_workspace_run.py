"""Check command status, input, and descendant cleanup in the Linux workspace supervisor."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "linux_workspace_run.py"


@unittest.skipUnless(sys.platform == "linux", "Linux process-group supervision")
class WorkspaceRunTests(unittest.TestCase):
    def test_exit_status(self):
        result = subprocess.run([sys.executable, str(RUNNER), "sh", "-c", "exit 23"])
        self.assertEqual(result.returncode, 23)

    def test_stdin_is_preserved(self):
        result = subprocess.run(
            [sys.executable, str(RUNNER), "cat"],
            input="workspace input\n", capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "workspace input\n")

    def test_signals_stop_descendants(self):
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=signum):
                self.check_cleanup(signum, "")

    def test_ignored_sigterm_is_escalated(self):
        self.check_cleanup(signal.SIGTERM, "trap '' TERM; ")

    def check_cleanup(self, signum, setup):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "pids"
            command = setup + 'sleep 60 & printf "%s %s" "$$" "$!" > "$1"; wait'
            process = subprocess.Popen(
                [sys.executable, str(RUNNER), "bash", "-c", command, "bash", str(pid_file)]
            )
            group = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if pid_file.exists() and len(pid_file.read_text().split()) == 2:
                        break
                    time.sleep(0.02)
                group, child = map(int, pid_file.read_text().split())
                process.send_signal(signum)
                self.assertEqual(process.wait(timeout=5), 128 + signum)
                for pid in (group, child):
                    stat = Path(f"/proc/{pid}/stat")
                    if stat.exists():
                        self.assertEqual(stat.read_text().split()[2], "Z")
            finally:
                if group is not None:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
                process.wait()


if __name__ == "__main__":
    unittest.main()
