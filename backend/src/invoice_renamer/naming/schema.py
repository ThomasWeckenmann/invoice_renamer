"""Pydantic contract for a proposed rename derived from an invoice extraction."""

from pydantic import BaseModel, Field

from invoice_renamer.extraction.models import InvoiceExtraction


class FilenameProposal(BaseModel):
    extraction: InvoiceExtraction
    proposed_filename: str
    requires_review: bool
    # Which of date/seller/product/amount/currency were missing, kept distinct from
    # extraction.warnings so the UI can explain why requires_review is true.
    missing_fields: list[str] = Field(default_factory=list)
