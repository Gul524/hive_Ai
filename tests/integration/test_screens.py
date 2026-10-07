import asyncio
from pathlib import Path

from hive.resources.manager import ResourceManager, ResourceState
from hive.screens.manager import LocalScreenManager


class FakeDisplay:
    def __init__(self):
        self.started = []
        self.stopped = []

    async def start(self, display: str, *, width: int = 1280, height: int = 800):
        self.started.append(display)
        return 12345, "ticks"

    async def stop(self, pid: int, start_ticks: str, display: str):
        self.stopped.append(display)
        return True

    async def screenshot(self, display: str, path: Path):
        path.write_bytes(b"PNG")


def test_virtual_screen_lifecycle_and_reuse(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = FakeDisplay()
        database = tmp_path / "hive.db"
        manager = LocalScreenManager(database, tmp_path, backend=backend)
        first = await manager.create(agent_id="a", task_id="t")
        screenshot = await manager.screenshot(first)
        assert screenshot.read_bytes() == b"PNG"
        assert manager.get(first).display == ":101"
        assert await LocalScreenManager(database, tmp_path, backend=backend).close(first)
        assert ResourceManager(database).list()[0].state == ResourceState.RELEASED
        second = await manager.create(agent_id="b", task_id="t2")
        assert manager.get(second).display == ":101"
        assert await manager.close(second)
    asyncio.run(scenario())
