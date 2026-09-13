"""FastAPI application factory for the local invoice-renamer worker."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from invoice_renamer.api.auth import require_session_token

# Tauri's webview origins, not real HTTP hosts: tauri://localhost on macOS/Linux,
# http://tauri.localhost on Windows.
_ALLOWED_ORIGINS = ["tauri://localhost", "http://tauri.localhost"]

# Printed to stdout once startup completes; the desktop shell watches the sidecar's
# stdout for this line instead of polling the HTTP port.
READY_MARKER = "INVOICE_RENAMER_WORKER_READY"


def create_app(*, announce_ready: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if announce_ready:
            print(READY_MARKER, flush=True)
        yield

    app = FastAPI(
        title="Invoice Renamer Worker",
        dependencies=[Depends(require_session_token)],
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
