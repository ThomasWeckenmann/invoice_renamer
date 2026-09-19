"""Decides whether embedded invoice XML alone can answer a filename and merges XML
values with model output when it can't. Shared by the analysis pipeline and, in its
opt-in extraction mode, the model-selection benchmark - see their own modules for
how each uses it.
"""

from enum import Enum

from pypdf import PdfReader

from invoice_renamer.documents.xml_attachments import (
    XmlDiscoveryResult,
    discover_invoice_xml,
)
from invoice_renamer.extraction.models import InvoiceExtraction, Language
from invoice_renamer.extraction.xml_adapter import extract_invoice_from_xml

# The fields build_filename_proposal() needs to produce a complete filename with
# nothing left for a human to fill in - see naming/builder.py's missing_fields.
FILENAME_FIELDS = ("invoice_date", "seller", "product_summary", "gross_total", "currency")


class ExtractionSource(str, Enum):
    XML = "xml"
    XML_AND_MODEL = "xml_and_model"
    MODEL = "model"


def route_xml(reader: PdfReader) -> tuple[XmlDiscoveryResult, InvoiceExtraction | None]:
    """Runs XML discovery and, only for a supported candidate, field mapping."""
    xml_result = discover_invoice_xml(reader)
    xml_extraction = (
        extract_invoice_from_xml(xml_result.candidate) if xml_result.candidate is not None else None
    )
    return xml_result, xml_extraction


def xml_fields_used(xml_extraction: InvoiceExtraction | None) -> list[str]:
    """Which fields XML actually supplied a validated value for, in the same order
    every caller reports them - drives both routing (xml_supplies_filename) and
    the extraction_source/xml_fields_used metrics reported to the user."""
    if xml_extraction is None:
        return []
    return [name for name in FILENAME_FIELDS if getattr(xml_extraction, name) is not None]


def xml_supplies_filename(xml_extraction: InvoiceExtraction | None) -> bool:
    return xml_extraction is not None and len(xml_fields_used(xml_extraction)) == len(
        FILENAME_FIELDS
    )


def merge_xml_and_model(
    xml_extraction: InvoiceExtraction | None, model_extraction: InvoiceExtraction
) -> InvoiceExtraction:
    """Combines a partial XML extraction with model output, field by field. A valid
    XML value always wins, even when the model disagrees; the model only fills
    fields XML left empty."""
    if xml_extraction is None:
        return model_extraction

    values = {
        name: (
            getattr(xml_extraction, name)
            if getattr(xml_extraction, name) is not None
            else getattr(model_extraction, name)
        )
        for name in FILENAME_FIELDS
    }
    language = (
        xml_extraction.language
        if xml_extraction.language != Language.UNKNOWN
        else model_extraction.language
    )
    return InvoiceExtraction(
        **values,
        language=language,
        evidence=xml_extraction.evidence,
        warnings=[*xml_extraction.warnings, *model_extraction.warnings],
    )
