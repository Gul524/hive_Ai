"""Build commands from validated structured inputs, never raw model shell text."""

from hive.policy.risk import PACKAGE, SERVICE


def package_install(package: str) -> list[str]:
    if not PACKAGE.fullmatch(package):
        raise ValueError("Invalid package name")
    return ["sudo", "dnf", "install", "-y", package]


def package_check(package: str) -> list[str]:
    if not PACKAGE.fullmatch(package):
        raise ValueError("Invalid package name")
    return ["rpm", "-q", package]


def service_start(service: str) -> list[str]:
    if not SERVICE.fullmatch(service):
        raise ValueError("Invalid service name")
    return ["sudo", "systemctl", "start", service]


def service_check(service: str) -> list[str]:
    if not SERVICE.fullmatch(service):
        raise ValueError("Invalid service name")
    return ["systemctl", "is-active", service]


def package_remove(package: str) -> list[str]:
    if not PACKAGE.fullmatch(package):
        raise ValueError("Invalid package name")
    return ["sudo", "dnf", "remove", "-y", package]


def package_refresh() -> list[str]:
    return ["sudo", "dnf", "makecache"]


def system_upgrade() -> list[str]:
    return ["sudo", "dnf", "upgrade", "-y"]


def service_action(action: str, service: str) -> list[str]:
    if action not in {"start", "stop", "enable", "disable"} or not SERVICE.fullmatch(service):
        raise ValueError("Invalid service operation")
    return ["sudo", "systemctl", action, service]


def firewall_add_port(port: int, protocol: str = "tcp") -> list[str]:
    if not 1 <= port <= 65535 or protocol not in {"tcp", "udp"}:
        raise ValueError("Invalid firewall port")
    return ["sudo", "firewall-cmd", "--permanent", f"--add-port={port}/{protocol}"]


def firewall_query_port(port: int, protocol: str = "tcp") -> list[str]:
    if not 1 <= port <= 65535 or protocol not in {"tcp", "udp"}:
        raise ValueError("Invalid firewall port")
    return ["firewall-cmd", f"--query-port={port}/{protocol}"]
