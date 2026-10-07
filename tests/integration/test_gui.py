import asyncio
from pathlib import Path

import pytest

from hive.core.approvals import ApprovalStore
from hive.core.checkpoint import TaskStore
from hive.core.models import TaskPhase
from hive.core.orchestrator import prepare_task_for_approval
from hive.gui.executor import execute_gui_plan
from hive.gui.plan import GuiPlanError, GuiPlanStore, create_gui_plan
from hive.resources.manager import ResourceManager
from hive.screens.manager import LocalScreenManager


class FakeDisplay:
    async def start(self, display: str):
        return 23456, "ticks"

    async def stop(self, pid: int, start_ticks: str, display: str):
        return True

    async def screenshot(self, display: str, path: Path):
        path.write_bytes(b"PNG")

    async def launch_firefox(self, display: str, profile: Path, url: str):
        assert profile.is_dir() and url == "https://example.com/"
        return 34567, "app-ticks"

    async def stop_firefox(self, pid: int, start_ticks: str, profile: Path):
        return True


class FakeInput:
    actions = []

    def __init__(self, **kwargs):
        pass

    async def click(self, x, y, *, approval_id):
        self.actions.append(("click", x, y))

    async def type_text(self, value, *, approval_id):
        self.actions.append(("type", value))

    async def key(self, key_name, *, approval_id):
        self.actions.append(("key", key_name))

    async def close(self):
        return True


def test_gui_requires_approvals_and_closes_display(tmp_path: Path) -> None:
    async def scenario():
        database = tmp_path / "hive.db"
        task = TaskStore(database).create("Click the button", agent_id="a")
        screens = LocalScreenManager(database, tmp_path, backend=FakeDisplay())
        screen_id = await screens.create(agent_id="a", task_id=task.task_id)
        reference = await screens.screenshot(screen_id)
        plan = create_gui_plan(task_id=task.task_id, agent_id="a", screen_id=screen_id,
                               kind="click", screens=screens, x=50, y=60,
                               reference_screenshot=reference)
        approvals = GuiPlanStore(database).add(plan)
        prepare_task_for_approval(database, plan, [r.approval_id for r in approvals])
        with pytest.raises(GuiPlanError, match="approval"):
            await execute_gui_plan(plan.plan_id, database_file=database, data_dir=tmp_path,
                                   screens=screens, adapter_factory=FakeInput,
                                   confidence_fn=lambda *a, **k: asyncio.sleep(0, result=1.0))
        assert screens.get(screen_id).status == "active"
        for approval in approvals:
            ApprovalStore(database).decide(approval.approval_id, approve=True)
        FakeInput.actions.clear()
        result = await execute_gui_plan(plan.plan_id, database_file=database,
                                        data_dir=tmp_path, screens=screens,
                                        adapter_factory=FakeInput,
                                        confidence_fn=lambda *a, **k: asyncio.sleep(0, result=1.0))
        assert FakeInput.actions == [("click", 50, 60)]
        assert Path(result["before"]).exists() and Path(result["after"]).exists()
        assert screens.get(screen_id).status == "active"
        assert TaskStore(database).get(task.task_id).phase == TaskPhase.DONE
        assert await screens.close(screen_id)
        assert not ResourceManager(database).list(leaks_only=True)
        with pytest.raises(GuiPlanError, match="already run"):
            await execute_gui_plan(plan.plan_id, database_file=database,
                                   data_dir=tmp_path, screens=screens,
                                   adapter_factory=FakeInput)
    asyncio.run(scenario())


def test_gui_rejects_changed_target_and_cleans_up(tmp_path: Path) -> None:
    async def scenario():
        database = tmp_path / "hive.db"
        task = TaskStore(database).create("Click", agent_id="a")
        screens = LocalScreenManager(database, tmp_path, backend=FakeDisplay())
        screen_id = await screens.create(agent_id="a", task_id=task.task_id)
        reference = await screens.screenshot(screen_id)
        plan = create_gui_plan(task_id=task.task_id, agent_id="a", screen_id=screen_id,
                               kind="click", screens=screens, x=10, y=10,
                               reference_screenshot=reference)
        approvals = GuiPlanStore(database).add(plan)
        prepare_task_for_approval(database, plan, [r.approval_id for r in approvals])
        for request in approvals:
            ApprovalStore(database).decide(request.approval_id, approve=True)
        FakeInput.actions.clear()
        with pytest.raises(GuiPlanError, match="confidence"):
            await execute_gui_plan(plan.plan_id, database_file=database,
                                   data_dir=tmp_path, screens=screens,
                                   adapter_factory=FakeInput,
                                   confidence_fn=lambda *a, **k: asyncio.sleep(0, result=0.1))
        assert not FakeInput.actions
        assert screens.get(screen_id).status == "closed"
        assert TaskStore(database).get(task.task_id).phase == TaskPhase.FAILED
        assert not ResourceManager(database).list(leaks_only=True)
    asyncio.run(scenario())


def test_gui_launch_keeps_session_for_next_approved_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario():
        async def public_url(url: str):
            assert url == "https://example.com/"
        monkeypatch.setattr("hive.screens.manager.validate_public_url", public_url)
        database = tmp_path / "hive.db"
        tasks = TaskStore(database)
        launch_task = tasks.create("Open site", agent_id="a")
        screens = LocalScreenManager(database, tmp_path, backend=FakeDisplay())
        screen_id = await screens.create(agent_id="a", task_id=launch_task.task_id)
        plan = create_gui_plan(task_id=launch_task.task_id, agent_id="a",
                               screen_id=screen_id, kind="launch", screens=screens,
                               url="https://example.com/")
        approvals = GuiPlanStore(database).add(plan)
        prepare_task_for_approval(database, plan, [r.approval_id for r in approvals])
        for request in approvals:
            ApprovalStore(database).decide(request.approval_id, approve=True)
        await execute_gui_plan(plan.plan_id, database_file=database, data_dir=tmp_path,
                               screens=screens, adapter_factory=FakeInput)
        assert screens.get(screen_id).app_pid == 34567
        next_task = tasks.create("Press tab", agent_id="a")
        next_plan = create_gui_plan(task_id=next_task.task_id, agent_id="a",
                                    screen_id=screen_id, kind="key", screens=screens,
                                    key_name="Tab")
        assert next_plan.task_id != screens.get(screen_id).task_id
        assert await screens.close(screen_id)
        assert not ResourceManager(database).list(leaks_only=True)
    asyncio.run(scenario())
