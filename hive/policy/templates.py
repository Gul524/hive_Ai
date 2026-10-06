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
