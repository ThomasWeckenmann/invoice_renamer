"""FastAPI application factory for the local invoice-renamer worker."""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from invoice_renamer.api.auth import require_session_token
from invoice_renamer.api.models_routes import ModelInstallCoordinator, models_router
from invoice_renamer.models.installer import resolve_data_dir

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

    # Created eagerly (not lazily on first request) - lazy getattr/setattr init
    # has its own race where two simultaneous first requests could each
    # construct a fresh coordinator and one would silently discard the other's
    # in-progress download state.
    app.state.model_install_coordinator = ModelInstallCoordinator(resolve_data_dir())
    app.include_router(models_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
