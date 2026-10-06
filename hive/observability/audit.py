"""Append-only local JSONL audit events with secret redaction."""

from __future__ import annotations

import json
import os
from pathlib import Path

from hive.core.models import utc_now
from hive.observability.logger import redact


class AuditLog:
    def __init__(self, path: Path):
        self.path = path

    def append(self, event: str, **fields: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = redact({"timestamp": utc_now().isoformat(), "event": event, **fields})
        data = (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
