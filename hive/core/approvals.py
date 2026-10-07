"""Persistent, expiring approval queue."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from hive.core.errors import HiveError
from hive.core.models import ApprovalRequest, ApprovalStatus
from hive.core.state import open_database
from hive.observability.audit import AuditLog


class ApprovalError(HiveError):
    """An approval cannot be found or changed."""


class ApprovalStore:
    def __init__(self, database_file: Path, *, audit_file: Path | None = None):
        self.database_file = database_file
        self.audit = AuditLog(audit_file or database_file.parent / "audit.jsonl")
        with open_database(database_file) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS approvals_queue
                    ON approvals(status, priority DESC, expires_at);
                CREATE TABLE IF NOT EXISTS approval_history (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    approval_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    note TEXT,
                    occurred_at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _save(connection: sqlite3.Connection, request: ApprovalRequest) -> None:
        connection.execute(
            "UPDATE approvals SET status=?, record_json=? WHERE approval_id=?",
            (request.status.value, request.model_dump_json(), request.approval_id),
        )

    @staticmethod
    def _event(connection: sqlite3.Connection, request: ApprovalRequest, note: str | None = None) -> None:
        connection.execute(
            "INSERT INTO approval_history (approval_id, status, note, occurred_at) VALUES (?, ?, ?, ?)",
            (request.approval_id, request.status.value, note, datetime.now(timezone.utc).isoformat()),
        )

    def add(self, request: ApprovalRequest) -> ApprovalRequest:
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalError("New approval must be pending")
        if request.risk_level.value == "forbidden":
            raise ApprovalError("Forbidden actions cannot be approved")
        if request.expires_at <= datetime.now(timezone.utc):
            raise ApprovalError("Approval expiry must be in the future")
        with open_database(self.database_file) as connection:
            try:
                connection.execute(
                    "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.approval_id, request.task_id, request.agent_id,
                        request.status.value, request.expires_at.isoformat(),
                        request.priority, request.model_dump_json(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ApprovalError("Approval ID already exists") from exc
            self._event(connection, request)
        self.audit.append("approval_requested", approval_id=request.approval_id,
                          task_id=request.task_id, agent_id=request.agent_id, plan_id=request.plan_id)
        return request

    def _expire(self, connection: sqlite3.Connection) -> None:
        now = datetime.now(timezone.utc)
        rows = connection.execute(
            "SELECT record_json FROM approvals WHERE status=? AND expires_at<=?",
            (ApprovalStatus.PENDING.value, now.isoformat()),
        ).fetchall()
        for (payload,) in rows:
            request = ApprovalRequest.model_validate_json(payload)
            request.status = ApprovalStatus.EXPIRED
            request.decided_at = now
            self._save(connection, request)
            self._event(connection, request)

    def get(self, approval_id: str) -> ApprovalRequest:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire(connection)
            row = connection.execute(
                "SELECT record_json FROM approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
        if row is None:
            raise ApprovalError(f"Unknown approval: {approval_id}")
        return ApprovalRequest.model_validate_json(row[0])

    def list(self, *, pending_only: bool = False, task_id: str | None = None,
             agent_id: str | None = None) -> list[ApprovalRequest]:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire(connection)
            query = "SELECT record_json FROM approvals WHERE 1=1"
            params: list[str] = []
            if pending_only:
                query += " AND status=?"
                params.append(ApprovalStatus.PENDING.value)
            if task_id:
                query += " AND task_id=?"
                params.append(task_id)
            if agent_id:
                query += " AND agent_id=?"
                params.append(agent_id)
            query += " ORDER BY priority DESC, expires_at ASC"
            rows = connection.execute(query, params).fetchall()
        return [ApprovalRequest.model_validate_json(row[0]) for row in rows]

    def decide(self, approval_id: str, *, approve: bool, note: str | None = None) -> ApprovalRequest:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire(connection)
            row = connection.execute(
                "SELECT record_json FROM approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if row is None:
                raise ApprovalError(f"Unknown approval: {approval_id}")
            request = ApprovalRequest.model_validate_json(row[0])
            if request.status != ApprovalStatus.PENDING:
                raise ApprovalError(f"Approval is already {request.status.value}")
            request.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
            request.notes = note
            request.decided_at = datetime.now(timezone.utc)
            self._save(connection, request)
            self._event(connection, request, note)
        self.audit.append("approval_approved" if approve else "approval_denied",
                          approval_id=request.approval_id, task_id=request.task_id,
                          agent_id=request.agent_id, note=note)
        from hive.core.checkpoint import TaskStore, TransitionError
        try:
            TaskStore(self.database_file).checkpoint(
                request.task_id, "approval_approved" if approve else "approval_denied",
                detail=request.approval_id,
            )
        except TransitionError:
            pass  # Standalone approval requests need not have a task record.
        from hive.observability.metrics import MetricsStore
        MetricsStore(self.database_file).add(
            "approval_wait_seconds", (request.decided_at - request.created_at).total_seconds()
        )
        return request

    def history(self, approval_id: str) -> list[dict]:
        self.get(approval_id)
        with open_database(self.database_file) as connection:
            rows = connection.execute(
                "SELECT status, note, occurred_at FROM approval_history WHERE approval_id=? ORDER BY event_id",
                (approval_id,),
            ).fetchall()
        return [dict(zip(("status", "note", "occurred_at"), row)) for row in rows]
