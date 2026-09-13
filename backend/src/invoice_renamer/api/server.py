"""Entrypoint that runs the FastAPI worker as a Tauri-managed sidecar process."""

import os

import uvicorn

from invoice_renamer.api.app import create_app

PORT_ENV_VAR = "INVOICE_RENAMER_PORT"
_HOST = "127.0.0.1"


def main() -> None:
    port_raw = os.environ.get(PORT_ENV_VAR)
    if not port_raw:
        raise RuntimeError(
            f"{PORT_ENV_VAR} must be set; the desktop shell picks the port at startup."
        )

    uvicorn.run(create_app(announce_ready=True), host=_HOST, port=int(port_raw), log_level="info")


if __name__ == "__main__":
    main()
