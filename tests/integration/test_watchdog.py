import asyncio
from datetime import timedelta
from pathlib import Path

from hive.agents.scheduler import LockStore
from hive.core.models import utc_now
from hive.core.state import open_database
from hive.observability.metrics import MetricsStore
from hive.resources.manager import ResourceHandle, ResourceManager, ResourceType
from hive.resources.watchdog import cleanup, scan
from hive.screens.manager import LocalScreenManager


class FakeDisplay:
    async def start(self, display: str):
        return 54321, "ticks"

    async def stop(self, pid: int, start_ticks: str, display: str):
        return True

    async def screenshot(self, display: str, path: Path):
        path.write_bytes(b"PNG")


def test_watchdog_reconciles_expired_owned_resources(tmp_path: Path) -> None:
    async def scenario():
        database = tmp_path / "hive.db"
        screens = LocalScreenManager(database, tmp_path, backend=FakeDisplay())
        screen_id = await screens.create(agent_id="a", task_id="t")
        record = screens.get(screen_id)
        record.expires_at = utc_now() - timedelta(minutes=1)
        screens._save(record)
        locks = LockStore(database)
        locks.acquire("test", agent_id="a", task_id="t", ttl_seconds=1)
        with open_database(database) as connection:
            connection.execute("UPDATE agent_locks SET expires_at=? WHERE lock_key='test'",
                               ((utc_now() - timedelta(seconds=1)).isoformat(),))
        resources = ResourceManager(database)
        handle = await resources.acquire(ResourceHandle(
            agent_id="a", task_id="t", resource_type=ResourceType.PROCESS,
            timeout_seconds=1, metadata={"pid": 99999999},
        ), lambda: asyncio.sleep(0, result=True))
        handle.last_used_at = utc_now() - timedelta(seconds=10)
        resources._save(handle)
        assert screen_id in scan(database, tmp_path).expired_screens
        report = await cleanup(database, tmp_path)
        assert report.expired_locks_removed == 1
        assert handle.resource_id in report.released_dead_processes
        assert screens.get(screen_id).status == "closed"
        assert not ResourceManager(database).list(leaks_only=True)
        MetricsStore(database).add("task_success")
        assert MetricsStore(database).list()[0]["count"] == 1
    asyncio.run(scenario())
