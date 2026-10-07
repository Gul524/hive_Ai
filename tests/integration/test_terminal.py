import asyncio
import shutil

import pytest

from hive.execution.executor import ExecutionError, run_command


@pytest.mark.skipif(not shutil.which("rpm"), reason="RPM is not installed")
def test_allowlisted_command_runs_in_pty() -> None:
    code, output, error = asyncio.run(run_command(["rpm", "-q", "rpm"],
                                                   timeout=10, use_pty=True))
    assert code == 0
    assert "rpm-" in output
    assert error == ""


def test_terminal_rejects_unsafe_environment() -> None:
    with pytest.raises(ExecutionError, match="environment"):
        asyncio.run(run_command(["rpm", "-q", "rpm"],
                                environment={"LD_PRELOAD": "/tmp/x"}))
