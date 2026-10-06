import asyncio
from pathlib import Path

import pytest

from hive.agents.registry import Agent, AgentRegistry
from hive.agents.scheduler import LockBusyError, LockStore, Scheduler


def test_agents_and_conflicting_locks(tmp_path: Path) -> None:
    database = tmp_path / "db"
    registry = AgentRegistry(database)
    first = registry.add(Agent(objective="one", workspace=tmp_path))
    second = registry.add(Agent(objective="two", workspace=tmp_path))
    assert len(registry.list()) == 2
    locks = LockStore(database)
    locks.acquire("package-manager", agent_id=first.agent_id, task_id="t1")
    with pytest.raises(LockBusyError):
        locks.acquire("package-manager", agent_id=second.agent_id, task_id="t2")
    locks.release("package-manager", agent_id=first.agent_id)
    locks.acquire("package-manager", agent_id=second.agent_id, task_id="t2")


def test_scheduler_runs_multiple_jobs() -> None:
    async def job(value: int) -> int:
        await asyncio.sleep(0.01)
        return value
    result = asyncio.run(Scheduler().run([lambda i=i: job(i) for i in (1, 2)]))
    assert result == [1, 2]
