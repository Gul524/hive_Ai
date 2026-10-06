from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class BrowserManager(ABC):
    @abstractmethod
    async def open(self, *, agent_id: str, task_id: str, isolated: bool = True) -> None: ...

    @abstractmethod
    async def screenshot(self, path: Path) -> Path: ...

    @abstractmethod
    async def close(self) -> bool: ...
