"""Acquire, release, and report task-owned resources."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from hive.core.models import new_id, utc_now
from hive.core.state import open_database


class ResourceType(StrEnum):
    PROCESS = "process"
    TEMP_FILE = "temp_file"
    BROWSER = "browser"
    BROWSER_PAGE = "browser_page"
    BROWSER_CONTEXT = "browser_context"
    BROWSER_PROCESS = "browser_process"
    SCREEN = "screen"
    LOCK = "lock"
    MODEL_REQUEST = "model_request"


class ResourceState(StrEnum):
    ACQUIRED = "acquired"
    RELEASING = "releasing"
    RELEASED = "released"
    RELEASE_FAILED = "release_failed"


class ResourceHandle(BaseModel):
    resource_id: str = Field(default_factory=new_id)
    agent_id: str
    task_id: str
    resource_type: ResourceType
    state: ResourceState = ResourceState.ACQUIRED
    created_at: datetime = Field(default_factory=utc_now)
    last_used_at: datetime = Field(default_factory=utc_now)
    timeout_seconds: int = Field(default=300, gt=0)
    metadata: dict = Field(default_factory=dict)


Cleanup = Callable[[], Awaitable[bool]]


class ResourceManager:
    def __init__(self, database_file: Path, *, max_per_agent: int = 10, cleanup_timeout: int = 10):
        self.database_file = database_file
        self.max_per_agent = max_per_agent
        self.cleanup_timeout = cleanup_timeout
        self._callbacks: dict[str, Cleanup] = {}
        self._lock = asyncio.Lock()
        with open_database(database_file) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS resources (resource_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, "
                "task_id TEXT NOT NULL, state TEXT NOT NULL, record_json TEXT NOT NULL)"
            )

    def _save(self, handle: ResourceHandle) -> None:
        with open_database(self.database_file) as connection:
            connection.execute(
                "INSERT INTO resources VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(resource_id) DO UPDATE SET state=excluded.state, record_json=excluded.record_json",
                (handle.resource_id, handle.agent_id, handle.task_id, handle.state.value,
                 handle.model_dump_json()),
            )

    def list(self, *, agent_id: str | None = None, leaks_only: bool = False) -> list[ResourceHandle]:
        with open_database(self.database_file) as connection:
            query = "SELECT record_json FROM resources WHERE 1=1"
            params: list[str] = []
            if agent_id:
                query += " AND agent_id=?"
                params.append(agent_id)
            if leaks_only:
                query += " AND state!=?"
                params.append(ResourceState.RELEASED.value)
            return [ResourceHandle.model_validate_json(row[0])
                    for row in connection.execute(query, params)]

    async def acquire(self, handle: ResourceHandle, cleanup: Cleanup) -> ResourceHandle:
        async with self._lock:
            if len(self.list(agent_id=handle.agent_id, leaks_only=True)) >= self.max_per_agent:
                raise RuntimeError("Agent resource quota reached")
            self._callbacks[handle.resource_id] = cleanup
            self._save(handle)
            return handle

    async def release(self, resource_id: str) -> bool:
        async with self._lock:
            handle = next((item for item in self.list() if item.resource_id == resource_id), None)
            if handle is None:
                raise KeyError(resource_id)
            if handle.state == ResourceState.RELEASED:
                return True
            handle.state = ResourceState.RELEASING
            self._save(handle)
            callback = self._callbacks.get(resource_id)
            try:
                verified = bool(callback and await asyncio.wait_for(callback(), self.cleanup_timeout))
            except Exception:
                verified = False
            handle.state = ResourceState.RELEASED if verified else ResourceState.RELEASE_FAILED
            handle.last_used_at = utc_now()
            self._save(handle)
            self._callbacks.pop(resource_id, None)
            return verified

    async def cleanup_all(self, *, task_id: str) -> list[str]:
        failed: list[str] = []
        for handle in self.list(leaks_only=True):
            if handle.task_id == task_id and not await self.release(handle.resource_id):
                failed.append(handle.resource_id)
        return failed

    def stale(self) -> list[ResourceHandle]:
        now = utc_now()
        return [item for item in self.list(leaks_only=True)
                if item.last_used_at + timedelta(seconds=item.timeout_seconds) < now]

    def mark_released(self, resource_id: str, *, verified: bool) -> None:
        """Reconcile a persistent resource after the acquiring process exited."""
        handle = next((item for item in self.list() if item.resource_id == resource_id), None)
        if handle is None:
            raise KeyError(resource_id)
        handle.state = ResourceState.RELEASED if verified else ResourceState.RELEASE_FAILED
        handle.last_used_at = utc_now()
        self._save(handle)
        self._callbacks.pop(resource_id, None)
