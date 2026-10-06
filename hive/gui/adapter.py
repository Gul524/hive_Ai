from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class GuiAdapter(ABC):
    @abstractmethod
    async def screenshot(self, path: Path) -> Path: ...

    @abstractmethod
    async def click(self, x: int, y: int, *, approval_id: str) -> None: ...

    @abstractmethod
    async def close(self) -> bool: ...
