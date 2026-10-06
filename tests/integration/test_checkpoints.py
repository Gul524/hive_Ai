from pathlib import Path

import pytest

from hive.core.checkpoint import TaskStore, TransitionError
from hive.core.models import TaskPhase, TaskStatus


def test_task_persists_transitions_and_events(tmp_path: Path) -> None:
    path = tmp_path / "hive.db"
    store = TaskStore(path)
    task = store.create("Create a test file")
    task = store.transition(task.task_id, TaskPhase.SUMMARIZE, state_patch={"summary": "safe"})
    reloaded = TaskStore(path).get(task.task_id)
    assert reloaded.phase == TaskPhase.SUMMARIZE
    assert reloaded.state["summary"] == "safe"
    assert len(store.events(task.task_id)) == 2
    assert len(store.list(interrupted_only=True)) == 1
    with pytest.raises(TransitionError):
        store.transition(task.task_id, TaskPhase.EXECUTE)


def test_resume_risky_task_returns_to_approval(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "hive.db")
    task = store.create("Do a safe task")
    for phase in (TaskPhase.SUMMARIZE, TaskPhase.OPTIONS, TaskPhase.RECOMMEND,
                  TaskPhase.DISCUSS, TaskPhase.WAIT_APPROVAL, TaskPhase.PLAN,
                  TaskPhase.PREFLIGHT, TaskPhase.EXECUTE):
        store.transition(task.task_id, phase)
    store.pause(task.task_id)
    resumed = TaskStore(tmp_path / "hive.db").resume(task.task_id)
    assert resumed.phase == TaskPhase.WAIT_APPROVAL
    assert resumed.status == TaskStatus.AWAITING_APPROVAL


def test_terminal_task_cannot_resume(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "hive.db")
    task = store.create("Cancel me")
    store.transition(task.task_id, TaskPhase.CANCELED)
    assert not store.list(interrupted_only=True)
    with pytest.raises(TransitionError):
        store.resume(task.task_id)
