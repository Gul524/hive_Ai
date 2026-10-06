"""Bounded concurrent jobs with cross-process SQLite locks."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import AsyncIterator

from hive.core.models import utc_now
from hive.core.state import open_database


class LockBusyError(Exception):
    pass


class LockStore:
    def __init__(self, database_file: Path):
        self.database_file = database_file
        with open_database(database_file) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS agent_locks (lock_key TEXT PRIMARY KEY, owner_agent TEXT NOT NULL, task_id TEXT NOT NULL, expires_at TEXT NOT NULL)")

    def acquire(self, key: str, *, agent_id: str, task_id: str, ttl_seconds: int = 600) -> None:
        now = utc_now()
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM agent_locks WHERE expires_at<=?", (now.isoformat(),))
            row = connection.execute("SELECT owner_agent FROM agent_locks WHERE lock_key=?", (key,)).fetchone()
            if row:
                raise LockBusyError(f"Lock {key} held by agent {row[0]}")
            connection.execute("INSERT INTO agent_locks VALUES (?, ?, ?, ?)",
                               (key, agent_id, task_id, (now + timedelta(seconds=ttl_seconds)).isoformat()))

    def release(self, key: str, *, agent_id: str) -> None:
        with open_database(self.database_file) as connection:
            connection.execute("DELETE FROM agent_locks WHERE lock_key=? AND owner_agent=?", (key, agent_id))

    @asynccontextmanager
    async def hold(self, key: str, *, agent_id: str, task_id: str) -> AsyncIterator[None]:
        self.acquire(key, agent_id=agent_id, task_id=task_id)
        try:
            yield
        finally:
            self.release(key, agent_id=agent_id)


class Scheduler:
    def __init__(self, *, max_active: int = 3, max_executing: int = 2):
        self.active = asyncio.Semaphore(max_active)
        self.executing = asyncio.Semaphore(max_executing)

    async def run(self, jobs: list) -> list:
        async def bounded(job):
            async with self.active:
                async with self.executing:
                    return await job()
        return await asyncio.gather(*(bounded(job) for job in jobs))
