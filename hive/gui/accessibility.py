from __future__ import annotations

from abc import ABC, abstractmethod


class AccessibilityAdapter(ABC):
    @abstractmethod
    async def find(self, *, role: str, name: str) -> tuple[int, int, float]: ...
