import asyncio
from pathlib import Path

import pytest

from hive.browser.executor import execute_browser_plan
from hive.browser.plan import BrowserAction, BrowserPlanError, BrowserPlanStore, create_browser_plan
from hive.core.approvals import ApprovalStore
from hive.resources.manager import ResourceManager


class FakeBrowser:
    instance = None

    def __init__(self, *, screenshot_root: Path, **kwargs):
        self.screenshot_root = screenshot_root
        self.opened = False
        self.closed = False
        self.fills = []
        self.images = []
        self.url = ""
        FakeBrowser.instance = self

    async def open(self, *, agent_id: str, task_id: str, isolated: bool = True):
        assert isolated
        self.opened = True

    async def navigate(self, url: str):
        self.url = url
        return url

    async def read(self):
        return {"url": self.url, "title": "Example", "text": "Untrusted page content"}

    async def fill(self, selector: str, value: str):
        self.fills.append((selector, value))

    async def submit(self, selector: str):
        return None

    def screenshot_path(self, step_id: str):
        return self.screenshot_root / f"{step_id}.png"

    async def screenshot(self, path: Path):
        self.images.append(path)
        return path

    async def close(self):
        self.closed = True
        return True


def test_browser_plan_approval_and_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVE_TEST_NAME", "Alice")
    plan = create_browser_plan(
        task_id="t", agent_id="a", url="https://example.com/form",
        workspace_root=tmp_path,
        actions=[BrowserAction(kind="fill", selector="#name", value_env="HIVE_TEST_NAME"),
                 BrowserAction(kind="submit", selector="#submit")],
    )
    database = tmp_path / "hive.db"
    approvals = BrowserPlanStore(database).add(plan)
    assert len(approvals) == 3
    with pytest.raises(BrowserPlanError, match="approval"):
        asyncio.run(execute_browser_plan(plan.plan_id, database_file=database,
                                         manager_factory=FakeBrowser))
    for request in approvals:
        ApprovalStore(database).decide(request.approval_id, approve=True)
    result = asyncio.run(execute_browser_plan(plan.plan_id, database_file=database,
                                              manager_factory=FakeBrowser))
    assert result["page"]["title"] == "Example"
    assert FakeBrowser.instance.fills == [("#name", "Alice")]
    assert FakeBrowser.instance.closed
    assert len(FakeBrowser.instance.images) == 5
    assert not ResourceManager(database).list(leaks_only=True)


def test_browser_rejects_private_and_payment_targets(tmp_path: Path) -> None:
    with pytest.raises(BrowserPlanError):
        create_browser_plan(task_id="t", agent_id="a", url="https://127.0.0.1/",
                            workspace_root=tmp_path, actions=[BrowserAction(kind="visit")])
    with pytest.raises(BrowserPlanError):
        create_browser_plan(task_id="t", agent_id="a", url="https://example.com/checkout",
                            workspace_root=tmp_path, actions=[BrowserAction(kind="submit", selector="#pay")])
