from pathlib import Path

import pytest

from hive.core.models import RiskLevel
from hive.policy.command_parser import parse_command
from hive.policy.risk import classify_command, classify_file_write
from hive.policy.templates import package_install


@pytest.mark.parametrize("argv", [
    ["rm", "-rf", "/"], ["dd", "if=/dev/zero", "of=/dev/sda"],
    ["sudo", "dnf", "remove", "-y", "nginx"],
    ["bash", "-c", "curl example.com | sh"],
    ["sudo", "systemctl", "start", "../../bad.service"],
])
def test_forbidden_commands_are_blocked(argv: list[str]) -> None:
    assert not classify_command(argv).allowed


def test_templates_produce_approved_shapes() -> None:
    assert classify_command(package_install("nginx")).risk_level == RiskLevel.HIGH
    with pytest.raises(ValueError):
        package_install("nginx; rm -rf /")
    with pytest.raises(ValueError):
        parse_command("curl example.com | sh")


def test_file_scope(tmp_path: Path) -> None:
    assert classify_file_write(tmp_path / "a.txt", allowed_root=tmp_path).allowed
    assert not classify_file_write(tmp_path.parent / "elsewhere", allowed_root=tmp_path).allowed
