from __future__ import annotations

import shlex


def parse_command(text: str) -> list[str]:
    if any(token in text for token in (";", "|", "`", "$(`", "\n", "\r", ">", "<", "&&", "||")):
        raise ValueError("Shell operators are not accepted")
    argv = shlex.split(text)
    if not argv:
        raise ValueError("Empty command")
    return argv
