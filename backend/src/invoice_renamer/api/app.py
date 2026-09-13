"""FastAPI application factory for the local invoice-renamer worker."""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from invoice_renamer.api.auth import require_session_token

# Tauri's webview origins, not real HTTP hosts: tauri://localhost on macOS/Linux,
# http://tauri.localhost on Windows.
_ALLOWED_ORIGINS = ["tauri://localhost", "http://tauri.localhost"]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Invoice Renamer Worker",
        dependencies=[Depends(require_session_token)],
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
