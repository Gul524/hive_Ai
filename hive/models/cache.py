"""Local response cache keyed by model request and schema/config versions."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from hive.core.models import utc_now
from hive.core.state import open_database
from hive.models.provider import ModelRequest, ModelResponse


class ModelCache:
    def __init__(self, database_file: Path, *, ttl_seconds: int = 3600):
        self.database_file = database_file
        self.ttl_seconds = ttl_seconds
        with open_database(database_file) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS model_cache (cache_key TEXT PRIMARY KEY, expires_at TEXT NOT NULL, response_json TEXT NOT NULL)")

    @staticmethod
    def key(request: ModelRequest, *, schema_version: str = "1", config_version: str = "1") -> str:
        payload = request.model_dump_json() + schema_version + config_version
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, request: ModelRequest) -> ModelResponse | None:
        with open_database(self.database_file) as connection:
            row = connection.execute("SELECT expires_at, response_json FROM model_cache WHERE cache_key=?",
                                     (self.key(request),)).fetchone()
        if row is None or row[0] <= utc_now().isoformat():
            return None
        return ModelResponse.model_validate_json(row[1])

    def put(self, request: ModelRequest, response: ModelResponse) -> None:
        expiry = utc_now() + timedelta(seconds=self.ttl_seconds)
        with open_database(self.database_file) as connection:
            connection.execute("INSERT INTO model_cache VALUES (?, ?, ?) "
                               "ON CONFLICT(cache_key) DO UPDATE SET expires_at=excluded.expires_at, response_json=excluded.response_json",
                               (self.key(request), expiry.isoformat(), response.model_dump_json()))
