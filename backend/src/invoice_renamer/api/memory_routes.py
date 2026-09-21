"""API routes exposing a live memory snapshot and manual model unloading for
the running worker."""

from fastapi import APIRouter, Request

from invoice_renamer.api.analyses_routes import AnalysisCoordinator
from invoice_renamer.inference.memory_status import MemorySnapshot, sample_memory

memory_router = APIRouter()


@memory_router.get("/memory")
def get_memory(request: Request) -> MemorySnapshot:
    coordinator: AnalysisCoordinator = request.app.state.analysis_coordinator
    snapshot = coordinator.runtime_snapshot()
    return sample_memory(
        runtime_device=snapshot.device,
        loaded_entry_id=snapshot.loaded_entry_id,
        loading=snapshot.loading,
        loading_entry_id=snapshot.loading_entry_id,
    )


@memory_router.delete("/memory/loaded-model", status_code=204)
def unload_model(request: Request) -> None:
    coordinator: AnalysisCoordinator = request.app.state.analysis_coordinator
    coordinator.request_unload()
