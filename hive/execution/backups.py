"""Durable file backups and reviewable restore plan creation."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from hive.core.models import new_id, utc_now
from hive.core.state import open_database
from hive.execution.command_plan import CommandPlan, PlanError, plan_file_write


class BackupRecord(BaseModel):
    backup_id: str = Field(default_factory=new_id)
    task_id: str
    agent_id: str
    original_path: Path
    sha256: str
    mode: int
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())


class BackupStore:
    def __init__(self, database_file: Path, backups_dir: Path):
        self.database_file = database_file
        self.backups_dir = backups_dir
        with open_database(database_file) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS file_backups (backup_id TEXT PRIMARY KEY, "
                "task_id TEXT NOT NULL, record_json TEXT NOT NULL)"
            )

    def path(self, record: BackupRecord) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", record.task_id) \
                or not re.fullmatch(r"[A-Za-z0-9_-]+", record.backup_id):
            raise PlanError("Invalid backup identifier")
        return self.backups_dir / record.task_id / record.backup_id / "original"

    def create(self, *, task_id: str, agent_id: str, original_path: Path) -> BackupRecord:
        original = original_path.resolve()
        if not original.is_file():
            raise PlanError("Backup source is missing")
        original_bytes = original.read_bytes()
        record = BackupRecord(task_id=task_id, agent_id=agent_id,
                              original_path=original,
                              sha256=hashlib.sha256(original_bytes).hexdigest(),
                              mode=original.stat().st_mode & 0o777)
        destination = self.path(record)
        destination.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            temp = destination.parent / ".original.tmp"
            shutil.copy2(original, temp)
            os.replace(temp, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != record.sha256:
                raise PlanError("Backup copy verification failed")
            metadata = destination.parent / "metadata.json"
            metadata.write_text(record.model_dump_json(indent=2), encoding="utf-8")
            metadata.chmod(0o600)
            with open_database(self.database_file) as connection:
                connection.execute("INSERT INTO file_backups VALUES (?, ?, ?)",
                                   (record.backup_id, task_id, record.model_dump_json()))
        except BaseException:
            shutil.rmtree(destination.parent, ignore_errors=True)
            raise
        return record

    def get(self, backup_id: str) -> BackupRecord:
        with open_database(self.database_file) as connection:
            row = connection.execute("SELECT record_json FROM file_backups WHERE backup_id=?",
                                     (backup_id,)).fetchone()
        if row is None:
            raise PlanError(f"Unknown backup: {backup_id}")
        return BackupRecord.model_validate_json(row[0])

    def list(self, *, task_id: str | None = None) -> list[BackupRecord]:
        with open_database(self.database_file) as connection:
            rows = connection.execute(
                "SELECT record_json FROM file_backups WHERE (? IS NULL OR task_id=?) ORDER BY rowid DESC",
                (task_id, task_id),
            ).fetchall()
        return [BackupRecord.model_validate_json(row[0]) for row in rows]

    def verified_content(self, backup_id: str) -> tuple[BackupRecord, str]:
        record = self.get(backup_id)
        source = self.path(record)
        if not source.is_file():
            raise PlanError("Backup file is missing")
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise PlanError("Backup file changed")
        try:
            return record, content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PlanError("Binary backup cannot use text restore") from exc

    def restore_plan(self, backup_id: str, *, task_id: str, agent_id: str,
                     workspace_root: Path) -> CommandPlan:
        record, content = self.verified_content(backup_id)
        return plan_file_write(task_id=task_id, agent_id=agent_id,
                               workspace_root=workspace_root,
                               target_path=record.original_path, content=content)
