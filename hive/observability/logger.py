"""Local JSONL logging with field-level secret redaction."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENSITIVE_FIELD = re.compile(r"(secret|password|passwd|token|api.?key|authorization|credential)", re.I)
SENSITIVE_TEXT = re.compile(
    r"(?i)(bearer\s+\S+|(?:api[_-]?key|password|token|secret)\s*[:=]\s*[^\s,;]+)"
)
PRIVATE_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")


def redact(value: Any, *, key: str = "") -> Any:
    if SENSITIVE_FIELD.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SENSITIVE_TEXT.sub("[REDACTED]", OPENAI_KEY.sub(
            "[REDACTED]", PRIVATE_KEY.sub("[REDACTED]", value)))
    return value


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "agent_id": getattr(record, "agent_id", None),
            "task_id": getattr(record, "task_id", None),
            "step_id": getattr(record, "step_id", None),
            "resource_id": getattr(record, "resource_id", None),
            "message": record.getMessage(),
        }
        if hasattr(record, "details"):
            payload["details"] = record.details
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def configure_logger(logs_dir: Path) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    logger = logging.getLogger("hive")
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(logs_dir / "hive.jsonl", encoding="utf-8")
    os.chmod(logs_dir / "hive.jsonl", 0o600)
    handler.setFormatter(JsonlFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
