"""Normalized representation of a PDF document, ready for field extraction."""

from pydantic import BaseModel, Field


class PageText(BaseModel):
    page_number: int
    text: str
    needs_ocr: bool


class NormalizedDocument(BaseModel):
    pages: list[PageText]
    embedded_xml: str | None = None
    warnings: list[str] = Field(default_factory=list)
