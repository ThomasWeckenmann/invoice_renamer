"""Tests for the live memory sampler: injected system/worker counters, the
GPU-in-use flag derived from the runtime device, and uninitialized runtimes
never reporting GPU use.
"""

from invoice_renamer.inference.memory_status import sample_memory


def _system_memory() -> tuple[int, int]:
    return 16_000_000_000, 8_000_000_000


def _worker_rss() -> int:
    return 1_200_000_000


def test_cpu_runtime_reports_gpu_not_in_use() -> None:
    snapshot = sample_memory(
        runtime_device="cpu", system_memory_fn=_system_memory, worker_rss_fn=_worker_rss
    )

    assert snapshot.gpu_in_use is False
    assert snapshot.runtime_device == "cpu"


def test_uninitialized_runtime_reports_gpu_not_in_use() -> None:
    snapshot = sample_memory(
        runtime_device=None, system_memory_fn=_system_memory, worker_rss_fn=_worker_rss
    )

    assert snapshot.runtime_device is None
    assert snapshot.gpu_in_use is False


def test_gpu_runtime_reports_gpu_in_use() -> None:
    snapshot = sample_memory(
        runtime_device="gpu", system_memory_fn=_system_memory, worker_rss_fn=_worker_rss
    )

    assert snapshot.runtime_device == "gpu"
    assert snapshot.gpu_in_use is True


def test_system_and_worker_readings_are_passed_through() -> None:
    snapshot = sample_memory(
        runtime_device="cpu", system_memory_fn=_system_memory, worker_rss_fn=_worker_rss
    )

    assert snapshot.system_total_bytes == 16_000_000_000
    assert snapshot.system_available_bytes == 8_000_000_000
    assert snapshot.worker_rss_bytes == 1_200_000_000
    assert snapshot.sampled_at > 0


def test_residency_and_loading_fields_default_to_unloaded() -> None:
    snapshot = sample_memory(
        runtime_device=None, system_memory_fn=_system_memory, worker_rss_fn=_worker_rss
    )

    assert snapshot.loaded_entry_id is None
    assert snapshot.loading is False
    assert snapshot.loading_entry_id is None


def test_residency_and_loading_fields_are_passed_through() -> None:
    snapshot = sample_memory(
        runtime_device="cpu",
        loaded_entry_id="model-a",
        loading=True,
        loading_entry_id="model-b",
        system_memory_fn=_system_memory,
        worker_rss_fn=_worker_rss,
    )

    assert snapshot.loaded_entry_id == "model-a"
    assert snapshot.loading is True
    assert snapshot.loading_entry_id == "model-b"
