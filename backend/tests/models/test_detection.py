"""Tests for hardware-capability detection."""

from pathlib import Path

import pytest

from invoice_renamer.models import detection
from invoice_renamer.models.capabilities import AccelerationBackend
from invoice_renamer.models.detection import detect_capabilities


def test_detect_capabilities_against_the_real_host() -> None:
    capabilities = detect_capabilities()

    assert capabilities.memory_gb > 0
    assert capabilities.free_disk_gb >= 0


def test_detect_capabilities_accepts_a_custom_disk_path(tmp_path: Path) -> None:
    capabilities = detect_capabilities(disk_path=tmp_path)

    assert capabilities.free_disk_gb >= 0


def test_apple_silicon_is_detected_as_mps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detection.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(detection.platform, "machine", lambda: "arm64")

    assert detection._detect_acceleration() is AccelerationBackend.MPS


def test_intel_mac_is_not_detected_as_mps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detection.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(detection.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(detection.shutil, "which", lambda _name: None)
    monkeypatch.setattr(detection.Path, "exists", lambda _self: False)

    assert detection._detect_acceleration() is AccelerationBackend.CPU


def test_nvidia_smi_present_is_detected_as_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detection.platform, "system", lambda: "Linux")
    monkeypatch.setattr(detection.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        detection.shutil,
        "which",
        lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
    )

    assert detection._detect_acceleration() is AccelerationBackend.CUDA


def test_rocm_smi_present_is_detected_as_rocm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detection.platform, "system", lambda: "Linux")
    monkeypatch.setattr(detection.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        detection.shutil, "which", lambda name: "/usr/bin/rocm-smi" if name == "rocm-smi" else None
    )
    monkeypatch.setattr(detection.Path, "exists", lambda _self: False)

    assert detection._detect_acceleration() is AccelerationBackend.ROCM


def test_no_accelerator_found_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detection.platform, "system", lambda: "Linux")
    monkeypatch.setattr(detection.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(detection.shutil, "which", lambda _name: None)
    monkeypatch.setattr(detection.Path, "exists", lambda _self: False)

    assert detection._detect_acceleration() is AccelerationBackend.CPU
