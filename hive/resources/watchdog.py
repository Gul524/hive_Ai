"""Crash recovery for resources that can be identified safely after a restart."""

from __future__ import annotations

from pathlib import Path
import asyncio
import os

from pydantic import BaseModel, Field

from hive.agents.scheduler import LockStore
from hive.core.checkpoint import TaskStore
from hive.core.models import utc_now
from hive.resources.manager import ResourceManager, ResourceType
from hive.screens.manager import LocalScreenManager


class RecoveryReport(BaseModel):
    interrupted_tasks: list[str] = Field(default_factory=list)
    stale_resources: list[str] = Field(default_factory=list)
    expired_screens: list[str] = Field(default_factory=list)
    expired_locks_removed: int = 0
    released_dead_processes: list[str] = Field(default_factory=list)
    cleanup_failed: list[str] = Field(default_factory=list)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def scan(database_file: Path, data_dir: Path) -> RecoveryReport:
    screens = LocalScreenManager(database_file, data_dir)
    return RecoveryReport(
        interrupted_tasks=[task.task_id for task in TaskStore(database_file).list(interrupted_only=True)],
        stale_resources=[item.resource_id for item in ResourceManager(database_file).stale()],
        expired_screens=[item.screen_id for item in screens.list(active_only=True)
                         if item.expires_at <= utc_now()],
    )


async def cleanup(database_file: Path, data_dir: Path) -> RecoveryReport:
    report = scan(database_file, data_dir)
    screens = LocalScreenManager(database_file, data_dir)
    for screen_id in report.expired_screens:
        if not await screens.close(screen_id):
            report.cleanup_failed.append(screen_id)
    report.expired_locks_removed = LockStore(database_file).prune_expired()
    resources = ResourceManager(database_file)
    for handle in resources.stale():
        if handle.resource_type != ResourceType.PROCESS:
            continue
        pid = handle.metadata.get("pid")
        if isinstance(pid, int) and not _process_exists(pid):
            resources.mark_released(handle.resource_id, verified=True)
            report.released_dead_processes.append(handle.resource_id)
    report.stale_resources = [item.resource_id for item in resources.stale()]
    return report


async def run_periodic(database_file: Path, data_dir: Path, *, interval_seconds: int,
                       stop_event: asyncio.Event | None = None) -> None:
    if interval_seconds < 1:
        raise ValueError("Watchdog interval must be positive")
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        await cleanup(database_file, data_dir)
        try:
            await asyncio.wait_for(stop_event.wait(), interval_seconds)
        except asyncio.TimeoutError:
            pass
