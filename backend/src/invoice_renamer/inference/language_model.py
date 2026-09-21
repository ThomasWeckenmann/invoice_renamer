"""Interface for the raw text-completion backend behind invoice extraction.

LlamaCppExtractor (the app runtime) and TransformersExtractor (the legacy
benchmark runtime) both implement this.
"""

from typing import Protocol


class LanguageModel(Protocol):
    def generate(self, prompt: str) -> str: ...
