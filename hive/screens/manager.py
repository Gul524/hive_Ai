from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ScreenManager(ABC):
    @abstractmethod
    async def create(self, *, agent_id: str, task_id: str) -> str: ...

    @abstractmethod
    async def screenshot(self, screen_id: str, path: Path) -> Path: ...

    @abstractmethod
    async def close(self, screen_id: str) -> bool: ...
