"""Interface for the raw text-completion backend behind invoice extraction.

TransformersExtractor implements this; it isn't built yet. Only this
interface and the shared parse/validate/repair orchestration in
`extractor.py` exist so far.
"""

from typing import Protocol


class LanguageModel(Protocol):
    def generate(self, prompt: str) -> str: ...
