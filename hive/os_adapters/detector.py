from pathlib import Path


def detect_distro(os_release: Path = Path("/etc/os-release")) -> str:
    values: dict[str, str] = {}
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    return values.get("ID", "unknown").lower()
