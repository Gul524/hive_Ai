"""Store redacted failure context for inspection and repair."""

from __future__ import annotations

import json
import os
import tempfile
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from hive.core.models import new_id, utc_now
from hive.core.state import open_database
from hive.observability.logger import redact


class FailureClass(StrEnum):
    APPROVAL = "approval"
    POLICY = "policy"
    COMMAND = "command"
    VERIFICATION = "verification"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


class FailedTaskRecord(BaseModel):
    failed_id: str = Field(default_factory=new_id)
    task_id: str
    agent_id: str
    objective: str
    plan_id: str
    failed_step_id: str | None = None
    failure_class: FailureClass
    error_message: str
    retry_count: int = 0
    logs_path: str
    screenshots_path: str | None = None
    resource_report: dict = Field(default_factory=dict)
    cleanup_report: dict = Field(default_factory=dict)
    suggested_fixes: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())


class FailureStore:
    def __init__(self, database_file: Path, artifacts_dir: Path):
        self.database_file = database_file
        self.artifacts_dir = artifacts_dir
        with open_database(database_file) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS failed_tasks (failed_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, record_json TEXT NOT NULL)")

    def add(self, record: FailedTaskRecord) -> FailedTaskRecord:
        record = FailedTaskRecord.model_validate(redact(record.model_dump()))
        directory = self.artifacts_dir / record.failed_id
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        payload = record.model_dump_json(indent=2)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".record-", delete=False) as stream:
            stream.write(payload)
            temp_path = Path(stream.name)
        os.replace(temp_path, directory / "task.json")
        os.chmod(directory / "task.json", 0o600)
        (directory / "error.log").write_text(record.error_message + "\n", encoding="utf-8")
        os.chmod(directory / "error.log", 0o600)
        for filename, contents in (
            ("steps.json", {"failed_step_id": record.failed_step_id}),
            ("resources.json", record.resource_report),
            ("cleanup_report.json", record.cleanup_report),
        ):
            artifact = directory / filename
            artifact.write_text(json.dumps(contents, indent=2), encoding="utf-8")
            os.chmod(artifact, 0o600)
        (directory / "logs").mkdir(mode=0o700)
        (directory / "screenshots").mkdir(mode=0o700)
        with open_database(self.database_file) as connection:
            connection.execute("INSERT INTO failed_tasks VALUES (?, ?, ?)",
                               (record.failed_id, record.task_id, payload))
        return record

    def list(self) -> list[FailedTaskRecord]:
        with open_database(self.database_file) as connection:
            return [FailedTaskRecord.model_validate_json(row[0]) for row in
                    connection.execute("SELECT record_json FROM failed_tasks ORDER BY rowid DESC")]

    def get(self, failed_id: str) -> FailedTaskRecord:
        with open_database(self.database_file) as connection:
            row = connection.execute("SELECT record_json FROM failed_tasks WHERE failed_id=?",
                                     (failed_id,)).fetchone()
        if row is None:
            raise KeyError(failed_id)
        return FailedTaskRecord.model_validate_json(row[0])
