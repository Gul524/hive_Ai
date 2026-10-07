"""Allow only deterministic Fedora command shapes."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from hive.core.models import RiskLevel

PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")
SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._\-]*\.service$")
PORT = re.compile(r"^(?:[1-9][0-9]{0,4})/(?:tcp|udp)$")


class PolicyDecision(BaseModel):
    allowed: bool
    risk_level: RiskLevel
    reason: str
    requires_step_approval: bool = False


def classify_command(argv: list[str]) -> PolicyDecision:
    def allow(risk: RiskLevel, reason: str) -> PolicyDecision:
        return PolicyDecision(allowed=True, risk_level=risk, reason=reason,
                              requires_step_approval=risk == RiskLevel.HIGH)

    deny = PolicyDecision(allowed=False, risk_level=RiskLevel.FORBIDDEN,
                          reason="Command is outside the deterministic allowlist")
    if not argv or any(not arg or "\x00" in arg for arg in argv):
        return deny
    if len(argv) == 3 and argv[:2] == ["rpm", "-q"] and PACKAGE.fullmatch(argv[2]):
        return allow(RiskLevel.LOW, "Read-only package query")
    if len(argv) == 3 and argv[:2] in (["systemctl", "is-active"],
                                         ["systemctl", "is-enabled"]) and SERVICE.fullmatch(argv[2]):
        return allow(RiskLevel.LOW, "Read-only service query")
    if argv in (["dnf", "repolist"], ["dnf", "history", "info", "last"]):
        return allow(RiskLevel.LOW, "Read-only package manager query")
    if ((len(argv) == 2 and argv[0] == "firewall-cmd" and argv[1].startswith("--query-port="))
            or (len(argv) == 3 and argv[:2] == ["firewall-cmd", "--permanent"]
                and argv[2].startswith("--query-port="))):
        candidate = argv[-1].removeprefix("--query-port=")
        if PORT.fullmatch(candidate) and int(candidate.split("/")[0]) <= 65535:
            return allow(RiskLevel.LOW, "Read-only firewall query")
    if len(argv) == 5 and argv[:3] == ["sudo", "dnf", "install"] and argv[3] == "-y" and PACKAGE.fullmatch(argv[4]):
        return allow(RiskLevel.HIGH, "Package installation changes the system")
    if len(argv) == 5 and argv[:3] == ["sudo", "dnf", "remove"] and argv[3] == "-y" and PACKAGE.fullmatch(argv[4]):
        return allow(RiskLevel.HIGH, "Package removal can remove dependencies")
    if argv == ["sudo", "dnf", "makecache"]:
        return allow(RiskLevel.MEDIUM, "Refresh package metadata")
    if argv == ["sudo", "dnf", "upgrade", "-y"]:
        return allow(RiskLevel.HIGH, "System upgrade changes installed packages")
    if len(argv) == 4 and argv[:3] in (["sudo", "systemctl", "start"],
                                          ["sudo", "systemctl", "stop"],
                                          ["sudo", "systemctl", "enable"],
                                          ["sudo", "systemctl", "disable"]) and SERVICE.fullmatch(argv[3]):
        return allow(RiskLevel.HIGH, "Service operation changes system state")
    if len(argv) == 3 and argv[:2] == ["sudo", "firewall-cmd"] and argv[2] == "--reload":
        return allow(RiskLevel.HIGH, "Reloading firewall changes active rules")
    if len(argv) == 4 and argv[:3] == ["sudo", "firewall-cmd", "--permanent"] \
            and argv[3].startswith("--add-port=") and PORT.fullmatch(argv[3].removeprefix("--add-port=")):
        port = int(argv[3].removeprefix("--add-port=").split("/")[0])
        if port <= 65535:
            return allow(RiskLevel.HIGH, "Opening a firewall port exposes a service")
    return deny


def classify_file_write(path: Path, *, allowed_root: Path) -> PolicyDecision:
    root = allowed_root.resolve()
    destination = path.resolve()
    if any(destination.is_relative_to(Path(system_path)) for system_path in
           ("/boot", "/dev", "/etc", "/proc", "/run", "/sys", "/usr", "/var")):
        return PolicyDecision(allowed=False, risk_level=RiskLevel.FORBIDDEN,
                              reason="Writing system paths is blocked by default")
    if destination == root or not destination.is_relative_to(root):
        return PolicyDecision(allowed=False, risk_level=RiskLevel.FORBIDDEN,
                              reason="File is outside the approved workspace")
    if destination.exists() and destination.is_dir():
        return PolicyDecision(allowed=False, risk_level=RiskLevel.FORBIDDEN,
                              reason="Destination is a directory")
    return PolicyDecision(allowed=True, risk_level=RiskLevel.MEDIUM,
                          reason="File write within the approved workspace")
