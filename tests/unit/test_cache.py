from pathlib import Path

from hive.models.cache import ModelCache
from hive.models.provider import ModelRequest, ModelResponse


def test_model_cache_key_and_expiry(tmp_path: Path) -> None:
    request = ModelRequest(messages=[{"role": "user", "content": "hi"}], model="local")
    response = ModelResponse(text="hello", model="local", provider="fake")
    cache = ModelCache(tmp_path / "hive.db", ttl_seconds=3600)
    assert cache.get(request) is None
    cache.put(request, response)
    assert cache.get(request).text == "hello"
    changed = request.model_copy(update={"model": "other"})
    assert cache.get(changed) is None
