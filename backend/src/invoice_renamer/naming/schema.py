"""Pydantic contract for a proposed rename derived from an invoice extraction."""

from pydantic import BaseModel

from invoice_renamer.extraction.models import InvoiceExtraction


class FilenameProposal(BaseModel):
    extraction: InvoiceExtraction
    proposed_filename: str
    requires_review: bool
