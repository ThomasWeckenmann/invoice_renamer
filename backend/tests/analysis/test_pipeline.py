"""Tests for run_document_analysis, the read -> extract -> filename pipeline behind a job."""

import json
from pathlib import Path

import pytest

from invoice_renamer.analysis.pipeline import run_document_analysis

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


class _FakeLanguageModel:
    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, prompt: str) -> str:
        return self._response


def test_happy_path_produces_the_same_proposal_as_the_e2e_test() -> None:
    pdf_bytes = (FIXTURES_DIR / "selectable_text_en.pdf").read_bytes()
    model_response = json.dumps(
        {
            "invoice_date": "2026-09-12",
            "seller": "Apple",
            "product_summary": "MacBook Air",
            "gross_total": "2180",
            "currency": "EUR",
            "language": "en",
            "warnings": [],
        }
    )

    proposal, metrics = run_document_analysis(
        pdf_bytes,
        _FakeLanguageModel(model_response),
        model_id="qwen3-0.6b",
        model_revision="c1899de289a04d12100db370d81485cdf75e47ca",
    )

    assert proposal.proposed_filename == "2026-09-12_Apple_MacBook-Air_2180-EUR.pdf"
    assert proposal.requires_review is False
    assert metrics.pages_total == 1
    assert metrics.total_ms >= 0
    assert metrics.pdf_extraction_ms >= 0
    assert metrics.ocr_ms >= 0
    assert metrics.inference_ms >= 0
    assert metrics.model_id == "qwen3-0.6b"
    assert metrics.model_revision == "c1899de289a04d12100db370d81485cdf75e47ca"
    assert metrics.provider == "transformers"


def test_a_corrupt_pdf_raises_instead_of_returning_a_synthesized_result() -> None:
    with pytest.raises(ValueError):
        run_document_analysis(
            b"not a pdf",
            _FakeLanguageModel("{}"),
            model_id="qwen3-0.6b",
            model_revision=None,
        )
