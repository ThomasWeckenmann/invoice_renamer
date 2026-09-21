"""Tests for the live memory sampler: injected counters per backend, partial
GPU failures, and uninitialized runtimes never touching a GPU provider.
"""

from invoice_renamer.inference.memory_status import GpuMemorySnapshot, sample_memory


def _system_memory() -> tuple[int, int]:
    return 16_000_000_000, 8_000_000_000


def _worker_rss() -> int:
    return 1_200_000_000


def test_cpu_runtime_never_calls_the_gpu_provider() -> None:
    def fail(device: str) -> tuple[GpuMemorySnapshot | None, str | None]:
        raise AssertionError("gpu_memory_fn must not be called for a CPU runtime")

    snapshot = sample_memory(
        runtime_device="cpu",
        system_memory_fn=_system_memory,
        worker_rss_fn=_worker_rss,
        gpu_memory_fn=fail,
    )

    assert snapshot.gpu is None
    assert snapshot.gpu_error is None
    assert snapshot.runtime_device == "cpu"


def test_uninitialized_runtime_never_calls_the_gpu_provider() -> None:
    def fail(device: str) -> tuple[GpuMemorySnapshot | None, str | None]:
        raise AssertionError("gpu_memory_fn must not be called before a model has loaded")

    snapshot = sample_memory(
        runtime_device=None,
        system_memory_fn=_system_memory,
        worker_rss_fn=_worker_rss,
        gpu_memory_fn=fail,
    )

    assert snapshot.runtime_device is None
    assert snapshot.gpu is None
    assert snapshot.gpu_error is None


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


def test_mps_gpu_reading_is_reported_under_driver_allocated_bytes() -> None:
    def gpu(device: str) -> tuple[GpuMemorySnapshot | None, str | None]:
        assert device == "mps"
        return GpuMemorySnapshot(backend="mps", driver_allocated_bytes=3_000_000_000), None

    snapshot = sample_memory(
        runtime_device="mps",
        system_memory_fn=_system_memory,
        worker_rss_fn=_worker_rss,
        gpu_memory_fn=gpu,
    )

    assert snapshot.gpu is not None
    assert snapshot.gpu.backend == "mps"
    assert snapshot.gpu.driver_allocated_bytes == 3_000_000_000
    assert snapshot.gpu.allocated_bytes is None
    assert snapshot.gpu.reserved_bytes is None
    assert snapshot.gpu_error is None


def test_cuda_gpu_reading_separates_allocated_from_reserved() -> None:
    def gpu(device: str) -> tuple[GpuMemorySnapshot | None, str | None]:
        assert device == "cuda"
        return (
            GpuMemorySnapshot(
                backend="cuda", allocated_bytes=2_000_000_000, reserved_bytes=2_500_000_000
            ),
            None,
        )

    snapshot = sample_memory(
        runtime_device="cuda",
        system_memory_fn=_system_memory,
        worker_rss_fn=_worker_rss,
        gpu_memory_fn=gpu,
    )

    assert snapshot.gpu is not None
    assert snapshot.gpu.backend == "cuda"
    assert snapshot.gpu.allocated_bytes == 2_000_000_000
    assert snapshot.gpu.reserved_bytes == 2_500_000_000
    assert snapshot.gpu.driver_allocated_bytes is None


def test_rocm_gpu_reading_uses_the_same_device_string_as_cuda() -> None:
    # ROCm-enabled torch exposes AMD GPUs through torch.cuda - see runtime.py -
    # so the sampler is handed "cuda" as the device but the provider can still
    # report a distinct backend label for display.
    def gpu(device: str) -> tuple[GpuMemorySnapshot | None, str | None]:
        assert device == "cuda"
        return (
            GpuMemorySnapshot(
                backend="rocm", allocated_bytes=1_000_000_000, reserved_bytes=1_200_000_000
            ),
            None,
        )

    snapshot = sample_memory(
        runtime_device="cuda",
        system_memory_fn=_system_memory,
        worker_rss_fn=_worker_rss,
        gpu_memory_fn=gpu,
    )

    assert snapshot.gpu is not None
    assert snapshot.gpu.backend == "rocm"


def test_gpu_unavailable_is_distinguishable_from_a_measured_zero() -> None:
    def gpu(device: str) -> tuple[GpuMemorySnapshot | None, str | None]:
        return None, None

    snapshot = sample_memory(
        runtime_device="mps",
        system_memory_fn=_system_memory,
        worker_rss_fn=_worker_rss,
        gpu_memory_fn=gpu,
    )

    assert snapshot.gpu is None
    assert snapshot.gpu_error is None


def test_gpu_provider_failure_returns_a_partial_snapshot_not_an_exception() -> None:
    def gpu(device: str) -> tuple[GpuMemorySnapshot | None, str | None]:
        raise RuntimeError("backend rejected the query mid-unload")

    snapshot = sample_memory(
        runtime_device="mps",
        system_memory_fn=_system_memory,
        worker_rss_fn=_worker_rss,
        gpu_memory_fn=gpu,
    )

    assert snapshot.gpu is None
    assert snapshot.gpu_error == "backend rejected the query mid-unload"
    # The rest of the snapshot is still populated despite the GPU failure.
    assert snapshot.system_total_bytes == 16_000_000_000
    assert snapshot.worker_rss_bytes == 1_200_000_000
