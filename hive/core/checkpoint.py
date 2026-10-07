"""Atomic SQLite task checkpoints and event history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from hive.core.errors import HiveError
from hive.core.models import Task, TaskPhase, TaskStatus, new_id, utc_now
from hive.core.state import open_database
from hive.voice.language import detect_language


class TransitionError(HiveError):
    """Task transition is invalid or task does not exist."""


NEXT_PHASES: dict[TaskPhase, set[TaskPhase]] = {
    TaskPhase.INTAKE: {TaskPhase.CLARIFY, TaskPhase.RESEARCH, TaskPhase.SUMMARIZE},
    TaskPhase.CLARIFY: {TaskPhase.RESEARCH, TaskPhase.SUMMARIZE},
    TaskPhase.RESEARCH: {TaskPhase.SUMMARIZE},
    TaskPhase.SUMMARIZE: {TaskPhase.OPTIONS},
    TaskPhase.OPTIONS: {TaskPhase.RECOMMEND},
    TaskPhase.RECOMMEND: {TaskPhase.DISCUSS},
    TaskPhase.DISCUSS: {TaskPhase.WAIT_APPROVAL},
    TaskPhase.WAIT_APPROVAL: {TaskPhase.PLAN},
    TaskPhase.PLAN: {TaskPhase.PREFLIGHT},
    TaskPhase.PREFLIGHT: {TaskPhase.EXECUTE},
    TaskPhase.EXECUTE: {TaskPhase.VERIFY},
    TaskPhase.VERIFY: {TaskPhase.RECOVER, TaskPhase.REPORT},
    TaskPhase.RECOVER: {TaskPhase.REPORT},
    TaskPhase.REPORT: {TaskPhase.DONE},
}

STATUS_FOR_PHASE: dict[TaskPhase, TaskStatus] = {
    TaskPhase.INTAKE: TaskStatus.CREATED,
    TaskPhase.CLARIFY: TaskStatus.CLARIFYING,
    TaskPhase.RESEARCH: TaskStatus.RESEARCHING,
    TaskPhase.SUMMARIZE: TaskStatus.DISCUSSING,
    TaskPhase.OPTIONS: TaskStatus.DISCUSSING,
    TaskPhase.RECOMMEND: TaskStatus.DISCUSSING,
    TaskPhase.DISCUSS: TaskStatus.DISCUSSING,
    TaskPhase.WAIT_APPROVAL: TaskStatus.AWAITING_APPROVAL,
    TaskPhase.PLAN: TaskStatus.PLANNING,
    TaskPhase.PREFLIGHT: TaskStatus.APPROVED,
    TaskPhase.EXECUTE: TaskStatus.EXECUTING,
    TaskPhase.VERIFY: TaskStatus.VERIFYING,
    TaskPhase.RECOVER: TaskStatus.FAILED,
    TaskPhase.REPORT: TaskStatus.VERIFYING,
    TaskPhase.DONE: TaskStatus.COMPLETED,
    TaskPhase.FAILED: TaskStatus.FAILED,
    TaskPhase.CANCELED: TaskStatus.CANCELED,
}


class TaskStore:
    def __init__(self, database_file: Path):
        self.database_file = database_file
        with open_database(database_file) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, updated_at);
                CREATE TABLE IF NOT EXISTS task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    detail TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                """
            )

    def create(self, objective: str, *, agent_id: str = "core", parent_task_id: str | None = None) -> Task:
        if not objective.strip():
            raise TransitionError("Task objective cannot be empty")
        task = Task(objective=objective, language=detect_language(objective),
                    agent_id=agent_id, parent_task_id=parent_task_id)
        task.checkpoint_id = new_id()
        with open_database(self.database_file) as connection:
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)",
                (task.task_id, task.agent_id, task.status.value, task.phase.value,
                 task.updated_at.isoformat(), task.model_dump_json()),
            )
            self._event(connection, task, "created")
        return task

    @staticmethod
    def _event(connection: sqlite3.Connection, task: Task, event_type: str,
               detail: str | None = None) -> None:
        connection.execute(
            "INSERT INTO task_events (task_id, checkpoint_id, event_type, phase, status, occurred_at, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task.task_id, task.checkpoint_id, event_type, task.phase.value,
             task.status.value, task.updated_at.isoformat(), detail),
        )

    @staticmethod
    def _read(connection: sqlite3.Connection, task_id: str) -> Task:
        row = connection.execute("SELECT record_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise TransitionError(f"Unknown task: {task_id}")
        return Task.model_validate_json(row[0])

    @staticmethod
    def _save(connection: sqlite3.Connection, task: Task, event_type: str,
              detail: str | None = None) -> None:
        task.updated_at = utc_now()
        task.checkpoint_id = new_id()
        connection.execute(
            "UPDATE tasks SET status=?, phase=?, updated_at=?, record_json=? WHERE task_id=?",
            (task.status.value, task.phase.value, task.updated_at.isoformat(),
             task.model_dump_json(), task.task_id),
        )
        TaskStore._event(connection, task, event_type, detail)

    def get(self, task_id: str) -> Task:
        with open_database(self.database_file) as connection:
            return self._read(connection, task_id)

    def list(self, *, interrupted_only: bool = False) -> list[Task]:
        with open_database(self.database_file) as connection:
            query = "SELECT record_json FROM tasks"
            if interrupted_only:
                query += " WHERE status NOT IN ('completed', 'failed', 'canceled')"
            query += " ORDER BY updated_at DESC"
            return [Task.model_validate_json(row[0]) for row in connection.execute(query)]

    def transition(self, task_id: str, phase: TaskPhase, *, detail: str | None = None,
                   state_patch: dict | None = None) -> Task:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._read(connection, task_id)
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}:
                raise TransitionError("Terminal task cannot transition")
            if task.status == TaskStatus.PAUSED:
                raise TransitionError("Resume paused task before transition")
            if phase not in NEXT_PHASES.get(task.phase, set()) and phase not in {
                TaskPhase.FAILED, TaskPhase.CANCELED,
            }:
                raise TransitionError(f"Invalid transition: {task.phase.value} -> {phase.value}")
            task.phase = phase
            task.status = STATUS_FOR_PHASE[phase]
            if state_patch:
                task.state.update(state_patch)
            self._save(connection, task, "transition", detail)
            return task

    def pause(self, task_id: str) -> Task:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._read(connection, task_id)
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED, TaskStatus.PAUSED}:
                raise TransitionError("Task cannot be paused")
            task.status = TaskStatus.PAUSED
            self._save(connection, task, "paused")
            return task

    def resume(self, task_id: str) -> Task:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._read(connection, task_id)
            if task.status != TaskStatus.PAUSED:
                raise TransitionError("Task is not paused")
            if task.phase in {TaskPhase.PREFLIGHT, TaskPhase.EXECUTE, TaskPhase.VERIFY}:
                task.phase = TaskPhase.WAIT_APPROVAL
            task.status = STATUS_FOR_PHASE[task.phase]
            self._save(connection, task, "resumed")
            return task

    def events(self, task_id: str) -> list[dict]:
        with open_database(self.database_file) as connection:
            self._read(connection, task_id)
            rows = connection.execute(
                "SELECT checkpoint_id, event_type, phase, status, occurred_at, detail "
                "FROM task_events WHERE task_id=? ORDER BY event_id", (task_id,),
            ).fetchall()
        names = ("checkpoint_id", "event_type", "phase", "status", "occurred_at", "detail")
        return [dict(zip(names, row)) for row in rows]

    def checkpoint(self, task_id: str, event_type: str, *, detail: str | None = None) -> Task:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._read(connection, task_id)
            self._save(connection, task, event_type, detail)
            return task

    def patch_state(self, task_id: str, changes: dict, *, event_type: str) -> Task:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._read(connection, task_id)
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}:
                raise TransitionError("Terminal task cannot be changed")
            task.state.update(changes)
            self._save(connection, task, event_type)
            return task
