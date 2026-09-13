"""Interface for the raw text-completion backend behind invoice extraction.

TransformersExtractor (local) and OpenRouterExtractor (cloud) both implement
this; neither is built yet. Only this interface and the shared parse/validate/
repair orchestration in `extractor.py` exist so far.
"""

from typing import Protocol


class LanguageModel(Protocol):
    def generate(self, prompt: str) -> str: ...
