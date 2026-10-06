"""Validate model JSON and allow one repair attempt."""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from hive.models.provider import ModelRequest
from hive.models.router import ModelRouter

T = TypeVar("T", bound=BaseModel)


async def structured_response(router: ModelRouter, request: ModelRequest, schema: type[T]) -> T:
    current = request
    for attempt in range(2):
        response = await router.complete(current)
        try:
            return schema.model_validate(json.loads(response.text))
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt:
                raise ValueError("Model produced invalid structured output twice") from exc
            current = request.model_copy(deep=True)
            current.messages.append({"role": "user", "content":
                f"Return only valid JSON matching this schema: {schema.model_json_schema()}"})
    raise AssertionError("unreachable")
