from datetime import timedelta
from pathlib import Path

import pytest

from hive.core.approvals import ApprovalError, ApprovalStore
from hive.core.diffs import unified_diff
from hive.core.models import ApprovalRequest, ApprovalStatus, RiskLevel, utc_now


def request(**changes: object) -> ApprovalRequest:
    data = dict(task_id="t1", agent_id="a1", plan_id="p1", title="Create file",
                description="Create a temporary file", risk_level=RiskLevel.LOW,
                verification_summary="file exists", rollback_summary="remove file",
                plan_digest="abc", expires_at=utc_now() + timedelta(minutes=10))
    data.update(changes)
    return ApprovalRequest(**data)


def test_approval_lifecycle_and_history(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "hive.db")
    item = store.add(request())
    assert [r.approval_id for r in store.list(pending_only=True, agent_id="a1")] == [item.approval_id]
    decided = store.decide(item.approval_id, approve=True, note="reviewed")
    assert decided.status == ApprovalStatus.APPROVED
    assert decided.notes == "reviewed"
    assert [event["status"] for event in store.history(item.approval_id)] == ["pending", "approved"]
    with pytest.raises(ApprovalError):
        store.decide(item.approval_id, approve=False)


def test_expiry_and_forbidden_actions(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path / "hive.db")
    with pytest.raises(ApprovalError):
        store.add(request(risk_level=RiskLevel.FORBIDDEN))
    item = store.add(request(expires_at=utc_now() + timedelta(milliseconds=20)))
    import time
    time.sleep(0.03)
    assert store.get(item.approval_id).status == ApprovalStatus.EXPIRED
    with pytest.raises(ApprovalError):
        store.decide(item.approval_id, approve=True)


def test_diff_shows_reviewable_change() -> None:
    diff = unified_diff("a\n", "b\n", path="settings.ini")
    assert "--- a/settings.ini" in diff
    assert "+b" in diff
