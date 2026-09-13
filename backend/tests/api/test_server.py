"""Tests that the sidecar entrypoint only announces readiness after a real bind."""

import os
import socket
import subprocess
import sys
from pathlib import Path

READY_MARKER = "INVOICE_RENAMER_WORKER_READY"
SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"


def _run_worker(port: int, token: str = "test-token", timeout: float = 5.0) -> str:
    env = os.environ.copy()
    env["INVOICE_RENAMER_SESSION_TOKEN"] = token
    env["INVOICE_RENAMER_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "-m", "invoice_renamer.api.server"],
        cwd=SRC_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=timeout)
    stdout = proc.stdout
    assert stdout is not None
    return stdout.read()


def test_ready_marker_is_not_printed_when_port_is_already_bound() -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]

    try:
        output = _run_worker(port)
    finally:
        blocker.close()

    assert READY_MARKER not in output


def test_ready_marker_is_printed_after_successful_bind() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    output = _run_worker(port)

    assert READY_MARKER in output
