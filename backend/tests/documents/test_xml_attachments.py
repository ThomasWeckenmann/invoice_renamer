"""Tests for embedded invoice XML discovery and classification - no page text, OCR,
or field mapping involved.
"""

from pathlib import Path

from invoice_renamer.documents.pdf_open import open_validated_pdf
from invoice_renamer.documents.xml_attachments import XmlDiscoveryStatus, discover_invoice_xml

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "fixtures"


def _discover(name: str) -> tuple[XmlDiscoveryStatus, str | None, str | None, str | None]:
    reader, _ = open_validated_pdf((FIXTURES_DIR / name).read_bytes())
    result = discover_invoice_xml(reader)
    return result.status, result.attachment_name, result.profile_id, result.warning


def test_ordinary_pdf_has_no_candidate() -> None:
    status, name, profile_id, warning = _discover("selectable_text_en.pdf")

    assert status == XmlDiscoveryStatus.NONE
    assert name is None
    assert profile_id is None
    assert warning is None


def test_complete_supported_xml_is_classified_supported() -> None:
    status, name, profile_id, warning = _discover("with_zugferd_xml.pdf")

    assert status == XmlDiscoveryStatus.SUPPORTED
    assert name == "factur-x.xml"
    assert profile_id == "urn:cen.eu:en16931:2017"
    assert warning is None


def test_malformed_xml_is_invalid_with_a_concise_warning() -> None:
    status, name, profile_id, warning = _discover("with_zugferd_xml_malformed.pdf")

    assert status == XmlDiscoveryStatus.INVALID
    assert name == "factur-x.xml"
    assert profile_id is None
    assert warning is not None
    assert len(warning) < 300


def test_wrong_root_element_is_unsupported() -> None:
    status, name, profile_id, warning = _discover("with_zugferd_xml_wrong_root.pdf")

    assert status == XmlDiscoveryStatus.UNSUPPORTED
    assert name == "factur-x.xml"
    assert profile_id is None
    assert warning is not None and "root" in warning


def test_recognized_but_unsupported_profile_is_unsupported() -> None:
    status, name, profile_id, warning = _discover("with_zugferd_xml_unsupported_profile.pdf")

    assert status == XmlDiscoveryStatus.UNSUPPORTED
    assert name == "factur-x.xml"
    assert profile_id == "urn:factur-x.eu:1p0:minimum"
    assert warning is not None and "profile" in warning


def test_identical_duplicate_attachments_still_resolve_supported() -> None:
    status, name, profile_id, warning = _discover("with_zugferd_xml_duplicate.pdf")

    assert status == XmlDiscoveryStatus.SUPPORTED
    assert profile_id == "urn:cen.eu:en16931:2017"
    assert warning is None


def test_distinct_invoice_candidates_are_ambiguous_not_first_wins() -> None:
    status, name, profile_id, warning = _discover("with_zugferd_xml_multiple_distinct.pdf")

    assert status == XmlDiscoveryStatus.AMBIGUOUS
    assert name is None
    assert warning is not None
    assert "factur-x.xml" in warning
    assert "xrechnung.xml" in warning
