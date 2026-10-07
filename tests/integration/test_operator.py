import asyncio
import json
from pathlib import Path

from hive.core.checkpoint import TaskStore
from hive.core.models import TaskPhase
from hive.core.operator import answer_clarification, make_reviewable_plan, propose
from hive.models.provider import ModelProvider, ModelRequest, ModelResponse
from hive.models.router import ModelRouter


class FakeModel(ModelProvider):
    def __init__(self, answers: list[dict]):
        self.answers = answers

    async def ping(self):
        return True

    async def complete(self, request: ModelRequest):
        return ModelResponse(text=json.dumps(self.answers.pop(0)), model=request.model,
                             provider="fake")


def _proposal(*, questions=None, action=None):
    return {
        "summary": "Install the web server after checking its package name.",
        "clarification_questions": questions or [],
        "options": [
            {"title": "Install", "benefit": "Local service", "cost_or_risk": "System package change"},
            {"title": "Skip", "benefit": "No change", "cost_or_risk": "Goal remains unmet"},
        ],
        "recommendation": "Install after approval.",
        "action": action,
    }


def test_operator_clarifies_then_creates_reviewable_fedora_plan(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr("hive.os_adapters.detector.detect_distro", lambda: "fedora")
    database = tmp_path / "hive.db"
    task = TaskStore(database).create("Install the web server")
    provider = FakeModel([
        _proposal(questions=["Which package?"], action=None),
        _proposal(action={"kind": "install_package", "package": "nginx"}),
    ])
    router = ModelRouter(provider)
    first = asyncio.run(propose(task.task_id, database_file=database,
                                router=router, model="fake"))
    assert first.clarification_questions
    assert TaskStore(database).get(task.task_id).phase == TaskPhase.CLARIFY
    answer_clarification(task.task_id, "nginx", database_file=database)
    second = asyncio.run(propose(task.task_id, database_file=database,
                                 router=router, model="fake"))
    assert second.action.package == "nginx"
    assert TaskStore(database).get(task.task_id).phase == TaskPhase.DISCUSS
    plan, approvals = make_reviewable_plan(task.task_id, database_file=database)
    assert plan.steps[0].command_argv == ["sudo", "dnf", "install", "-y", "nginx"]
    assert len(approvals) == 2
    assert TaskStore(database).get(task.task_id).phase == TaskPhase.WAIT_APPROVAL
