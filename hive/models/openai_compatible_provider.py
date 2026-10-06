from __future__ import annotations

import httpx

from hive.models.provider import ModelProvider, ModelRequest, ModelResponse


class OpenAICompatibleProvider(ModelProvider):
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.base_url}/models", headers={"Authorization": f"Bearer {self.api_key}"}
                )
                return response.is_success
        except httpx.HTTPError:
            return False

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": request.model, "messages": request.messages,
                      "temperature": request.temperature, "max_tokens": request.max_tokens},
            )
            response.raise_for_status()
            data = response.json()
            return ModelResponse(text=data["choices"][0]["message"]["content"],
                                 model=data.get("model", request.model), provider="openai_compatible")
