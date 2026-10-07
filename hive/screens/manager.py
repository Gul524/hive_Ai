from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
import os
import shutil

from pydantic import BaseModel, Field

from hive.core.models import new_id, utc_now
from hive.core.state import open_database
from hive.resources.manager import ResourceHandle, ResourceManager, ResourceType
from hive.screens.virtual_display import XvfbBackend
from hive.browser.safety import validate_public_url


class ScreenManager(ABC):
    @abstractmethod
    async def create(self, *, agent_id: str, task_id: str) -> str: ...

    @abstractmethod
    async def screenshot(self, screen_id: str, path: Path) -> Path: ...

    @abstractmethod
    async def close(self, screen_id: str) -> bool: ...


class ScreenRecord(BaseModel):
    screen_id: str = Field(default_factory=new_id)
    agent_id: str
    task_id: str
    display: str
    pid: int = 0
    start_ticks: str = ""
    resource_id: str | None = None
    app_pid: int = 0
    app_start_ticks: str = ""
    app_profile: Path | None = None
    status: str = "starting"
    screenshot_path: Path | None = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime = Field(default_factory=lambda: utc_now() + timedelta(minutes=30))


class LocalScreenManager(ScreenManager):
    def __init__(self, database_file: Path, data_dir: Path, *, backend: XvfbBackend | None = None):
        self.database_file = database_file
        self.data_dir = data_dir
        self.backend = backend or XvfbBackend()
        self.resources = ResourceManager(database_file)
        with open_database(database_file) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS screens (screen_id TEXT PRIMARY KEY, display TEXT NOT NULL, "
                "status TEXT NOT NULL, record_json TEXT NOT NULL)"
            )
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS screens_active_display "
                               "ON screens(display) WHERE status!='closed'")

    def _save(self, record: ScreenRecord) -> None:
        with open_database(self.database_file) as connection:
            connection.execute(
                "UPDATE screens SET status=?, record_json=? WHERE screen_id=?",
                (record.status, record.model_dump_json(), record.screen_id),
            )

    def get(self, screen_id: str) -> ScreenRecord:
        with open_database(self.database_file) as connection:
            row = connection.execute("SELECT record_json FROM screens WHERE screen_id=?",
                                     (screen_id,)).fetchone()
        if row is None:
            raise KeyError(screen_id)
        return ScreenRecord.model_validate_json(row[0])

    def list(self, *, active_only: bool = False) -> list[ScreenRecord]:
        with open_database(self.database_file) as connection:
            query = "SELECT record_json FROM screens"
            if active_only:
                query += " WHERE status!='closed'"
            return [ScreenRecord.model_validate_json(row[0]) for row in connection.execute(query)]

    async def create(self, *, agent_id: str, task_id: str) -> str:
        with open_database(self.database_file) as connection:
            connection.execute("BEGIN IMMEDIATE")
            used = {row[0] for row in connection.execute(
                "SELECT display FROM screens WHERE status!='closed'")}
            display = next((f":{number}" for number in range(101, 151)
                            if f":{number}" not in used
                            and not Path(f"/tmp/.X11-unix/X{number}").exists()), None)
            if display is None:
                raise RuntimeError("No virtual display slots are available")
            record = ScreenRecord(agent_id=agent_id, task_id=task_id, display=display)
            connection.execute("INSERT INTO screens VALUES (?, ?, ?, ?)",
                               (record.screen_id, display, record.status, record.model_dump_json()))
        try:
            pid, start_ticks = await self.backend.start(display)
            record.pid = pid
            record.start_ticks = start_ticks
            handle = await self.resources.acquire(ResourceHandle(
                agent_id=agent_id, task_id=task_id, resource_type=ResourceType.SCREEN,
                timeout_seconds=1800, metadata={"display": display, "pid": pid,
                                               "start_ticks": start_ticks},
            ), lambda: self.backend.stop(pid, start_ticks, display))
            record.resource_id = handle.resource_id
            record.status = "active"
            self._save(record)
            return record.screen_id
        except BaseException:
            if record.pid:
                await self.backend.stop(record.pid, record.start_ticks, display)
            record.status = "closed"
            self._save(record)
            raise

    async def screenshot(self, screen_id: str, path: Path | None = None) -> Path:
        record = self.get(screen_id)
        if record.status != "active":
            raise RuntimeError("Screen is not active")
        root = self.data_dir / "agents" / record.agent_id / "screenshots"
        destination = path or root / f"{utc_now().strftime('%Y%m%dT%H%M%S%fZ')}_screen.png"
        destination = destination.resolve()
        if not destination.is_relative_to(root.resolve()):
            raise ValueError("Screenshot path is outside the agent directory")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        await self.backend.screenshot(record.display, destination)
        os.chmod(destination, 0o600)
        record.screenshot_path = destination
        record.expires_at = utc_now() + timedelta(minutes=30)
        self._save(record)
        return destination

    async def launch_firefox(self, screen_id: str, url: str) -> None:
        record = self.get(screen_id)
        if record.status != "active" or record.app_pid:
            raise RuntimeError("Screen is closed or already has an application")
        await validate_public_url(url)
        profile = (self.data_dir / "agents" / record.agent_id / "profiles" / record.screen_id).resolve()
        profile.mkdir(parents=True, exist_ok=False, mode=0o700)
        try:
            pid, ticks = await self.backend.launch_firefox(record.display, profile, url)
        except BaseException:
            shutil.rmtree(profile, ignore_errors=True)
            raise
        record.app_pid = pid
        record.app_start_ticks = ticks
        record.app_profile = profile
        record.expires_at = utc_now() + timedelta(minutes=30)
        self._save(record)

    async def close(self, screen_id: str) -> bool:
        record = self.get(screen_id)
        if record.status == "closed":
            return True
        app_closed = True
        if record.app_pid and record.app_profile:
            app_closed = await self.backend.stop_firefox(
                record.app_pid, record.app_start_ticks, record.app_profile)
            if app_closed:
                profile = record.app_profile.resolve()
                root = (self.data_dir / "agents" / record.agent_id / "profiles").resolve()
                if profile.is_relative_to(root) and profile.name == record.screen_id:
                    shutil.rmtree(profile, ignore_errors=True)
        display_closed = await self.backend.stop(record.pid, record.start_ticks, record.display)
        verified = app_closed and display_closed
        if record.resource_id:
            self.resources.mark_released(record.resource_id, verified=verified)
        record.status = "closed" if verified else "release_failed"
        self._save(record)
        return verified

    async def cleanup_expired(self) -> list[str]:
        failed: list[str] = []
        for record in self.list(active_only=True):
            if record.expires_at <= utc_now() and not await self.close(record.screen_id):
                failed.append(record.screen_id)
        return failed
