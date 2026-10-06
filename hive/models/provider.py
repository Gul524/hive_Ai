from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from pydantic import BaseModel, Field


class ModelRequest(BaseModel):
    messages: list[dict[str, str]]
    model: str
    temperature: float = 0.2
    max_tokens: int = Field(default=1024, gt=0)


class ModelResponse(BaseModel):
    text: str
    model: str
    provider: str


class ModelProvider(ABC):
    @abstractmethod
    async def ping(self) -> bool: ...

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse: ...

    async def stream(self, request: ModelRequest) -> AsyncIterator[str]:
        yield (await self.complete(request)).text
