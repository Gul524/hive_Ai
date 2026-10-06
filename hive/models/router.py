"""Bounded, local-first model requests."""

from __future__ import annotations

import asyncio

from hive.models.provider import ModelProvider, ModelRequest, ModelResponse
from hive.models.cache import ModelCache
from hive.observability.logger import redact


class ModelUnavailableError(Exception):
    pass


class ModelRouter:
    def __init__(self, local: ModelProvider, *, cloud: ModelProvider | None = None,
                 cloud_enabled: bool = False, cloud_model: str | None = None,
                 local_limit: int = 1, cloud_limit: int = 2,
                 cache: ModelCache | None = None):
        self.local = local
        self.cloud = cloud
        self.cloud_enabled = cloud_enabled
        self.cloud_model = cloud_model
        self.cache = cache
        self.local_slots = asyncio.Semaphore(local_limit)
        self.cloud_slots = asyncio.Semaphore(cloud_limit)

    async def complete(self, request: ModelRequest, *, allow_cloud: bool = False) -> ModelResponse:
        request = request.model_copy(deep=True)
        request.messages = redact(request.messages)
        if self.cache:
            cached = self.cache.get(request)
            if cached and cached.provider != "openai_compatible":
                return cached
        try:
            async with self.local_slots:
                response = await self.local.complete(request)
                if self.cache:
                    self.cache.put(request, response)
                return response
        except Exception as exc:
            if not (allow_cloud and self.cloud_enabled and self.cloud):
                raise ModelUnavailableError("Local model unavailable; cloud fallback not permitted") from exc
        async with self.cloud_slots:
            cloud_request = request.model_copy(update={"model": self.cloud_model}) if self.cloud_model else request
            return await self.cloud.complete(cloud_request)
