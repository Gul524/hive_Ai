"""Browser action risk and URL restrictions."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from urllib.parse import urlsplit

from hive.core.models import RiskLevel

PAYMENT = re.compile(r"(?:payment|checkout|purchase|buy-now|pay-now|billing)", re.I)


def url_is_public_https(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


async def validate_public_url(url: str) -> None:
    if not url_is_public_https(url):
        raise ValueError("Browser only accepts public HTTPS URLs")
    host = urlsplit(url).hostname
    assert host is not None
    addresses = await asyncio.get_running_loop().getaddrinfo(host, 443)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("Browser URL resolves to a private network")


def browser_risk(kind: str, *, url: str, selector: str | None = None) -> RiskLevel:
    if PAYMENT.search(url) or (selector and PAYMENT.search(selector)):
        return RiskLevel.FORBIDDEN
    if kind in {"visit", "read"}:
        return RiskLevel.LOW
    if kind == "click":
        return RiskLevel.MEDIUM
    if kind in {"fill", "submit", "download", "upload", "login"}:
        return RiskLevel.HIGH
    return RiskLevel.FORBIDDEN
