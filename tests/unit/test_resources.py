import asyncio
from pathlib import Path

from hive.resources.manager import ResourceHandle, ResourceManager, ResourceState, ResourceType
from hive.execution.executor import run_command


def test_resource_cleanup_and_leak_report(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = ResourceManager(tmp_path / "hive.db", max_per_agent=1)
        handle = ResourceHandle(agent_id="a", task_id="t", resource_type=ResourceType.TEMP_FILE)
        async def cleanup() -> bool:
            return True
        await manager.acquire(handle, cleanup)
        assert len(manager.list(leaks_only=True)) == 1
        assert await manager.release(handle.resource_id)
        assert manager.list()[0].state == ResourceState.RELEASED
        assert not manager.list(leaks_only=True)
    asyncio.run(scenario())


def test_failed_cleanup_is_reported(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = ResourceManager(tmp_path / "hive.db")
        handle = ResourceHandle(agent_id="a", task_id="t", resource_type=ResourceType.TEMP_FILE)
        async def cleanup() -> bool:
            return False
        await manager.acquire(handle, cleanup)
        assert not await manager.release(handle.resource_id)
        assert manager.list(leaks_only=True)[0].state == ResourceState.RELEASE_FAILED
    asyncio.run(scenario())


def test_command_process_is_released(tmp_path: Path) -> None:
    import shutil
    if not shutil.which("rpm"):
        return

    async def scenario() -> None:
        manager = ResourceManager(tmp_path / "hive.db")
        await run_command(["rpm", "-q", "rpm"], timeout=10, resources=manager,
                          task_id="t", agent_id="a")
        assert not manager.list(leaks_only=True)
        assert manager.list()[0].state == ResourceState.RELEASED

    asyncio.run(scenario())
