from __future__ import annotations

from abc import ABC, abstractmethod


class TextToSpeech(ABC):
    @abstractmethod
    async def synthesize(self, text: str, *, language: str) -> bytes: ...
