from __future__ import annotations

import httpx

from hive.models.provider import ModelProvider, ModelRequest, ModelResponse


class OllamaProvider(ModelProvider):
    def __init__(self, base_url: str, *, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.is_success
        except httpx.HTTPError:
            return False

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/chat",
                json={"model": request.model, "messages": request.messages,
                      "stream": False, "options": {"temperature": request.temperature,
                                                    "num_predict": request.max_tokens}},
            )
            response.raise_for_status()
            data = response.json()
            return ModelResponse(text=data["message"]["content"],
                                 model=data.get("model", request.model), provider="ollama")
