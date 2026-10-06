import asyncio

import pytest
from pydantic import BaseModel

from hive.models.provider import ModelProvider, ModelRequest, ModelResponse
from hive.models.router import ModelRouter, ModelUnavailableError
from hive.models.structured import structured_response


class FakeProvider(ModelProvider):
    def __init__(self, texts: list[str]):
        self.texts = iter(texts)
        self.calls = 0

    async def ping(self) -> bool:
        return True

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(text=next(self.texts), model=request.model, provider="fake")


class Answer(BaseModel):
    answer: str


def test_structured_output_repairs_once() -> None:
    provider = FakeProvider(["bad json", '{"answer":"ok"}'])
    router = ModelRouter(provider)
    answer = asyncio.run(structured_response(router, ModelRequest(messages=[], model="test"), Answer))
    assert answer.answer == "ok"
    assert provider.calls == 2


def test_cloud_fallback_requires_opt_in() -> None:
    class Broken(FakeProvider):
        async def complete(self, request: ModelRequest) -> ModelResponse:
            raise ConnectionError("offline")
    router = ModelRouter(Broken([]), cloud=FakeProvider(['{"answer":"cloud"}']), cloud_enabled=True)
    with pytest.raises(ModelUnavailableError):
        asyncio.run(router.complete(ModelRequest(messages=[], model="test")))
    assert asyncio.run(router.complete(ModelRequest(messages=[], model="test"), allow_cloud=True)).provider == "fake"
