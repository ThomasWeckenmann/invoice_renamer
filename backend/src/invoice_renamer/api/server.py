"""Entrypoint that runs the FastAPI worker as a Tauri-managed sidecar process."""

import os
import socket

import uvicorn

from invoice_renamer.api.app import create_app

PORT_ENV_VAR = "INVOICE_RENAMER_PORT"
_HOST = "127.0.0.1"

# Printed to stdout once the server has actually bound its socket; the desktop
# shell watches the sidecar's stdout for this line instead of polling the port.
READY_MARKER = "INVOICE_RENAMER_WORKER_READY"


class _ReadyAnnouncingServer(uvicorn.Server):
    """Prints the ready marker only after the listening socket is bound.

    uvicorn's own startup() runs the ASGI app's lifespan *before* binding the
    socket, so a lifespan-based marker would fire even if the bind itself
    later fails (e.g. the port is already in use).
    """

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup()
        if self.started:
            print(READY_MARKER, flush=True)


def main() -> None:
    port_raw = os.environ.get(PORT_ENV_VAR)
    if not port_raw:
        raise RuntimeError(
            f"{PORT_ENV_VAR} must be set; the desktop shell picks the port at startup."
        )

    config = uvicorn.Config(create_app(), host=_HOST, port=int(port_raw), log_level="info")
    _ReadyAnnouncingServer(config).run()


if __name__ == "__main__":
    main()
