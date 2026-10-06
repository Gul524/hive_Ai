import os
import re
import subprocess
import sys
from pathlib import Path

from hive.core.approvals import ApprovalStore
from hive.core.checkpoint import TaskStore
from hive.core.models import TaskStatus


def test_cli_plan_approval_execution(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(HIVE_CONFIG_DIR=str(tmp_path / "config"),
               HIVE_DATA_DIR=str(tmp_path / "data"),
               HIVE_STATE_DIR=str(tmp_path / "state"))

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-m", "hive", *args], env=env, text=True,
                              capture_output=True, timeout=20)

    created = run("task", "create", "Write a safe file")
    task_id = re.search(r"Task: ([0-9a-f]{32})", created.stdout).group(1)
    target = tmp_path / "workspace/result.txt"
    planned = run("plan", "write-file", task_id, str(target),
                  "--workspace", str(target.parent), "--content", "hello")
    plan_id = re.search(r"Plan ID: ([0-9a-f]{32})", planned.stdout).group(1)
    denied = run("run", plan_id)
    assert denied.returncode == 1
    assert "approval is missing" in denied.stderr
    assert not target.exists()
    approval_id = ApprovalStore(tmp_path / "data/hive.db").list()[0].approval_id
    assert run("approval", "approve", approval_id).returncode == 0
    assert run("run", plan_id).returncode == 0
    assert target.read_text() == "hello"
    assert TaskStore(tmp_path / "data/hive.db").get(task_id).status == TaskStatus.COMPLETED
