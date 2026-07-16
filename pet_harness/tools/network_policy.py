from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse


Resolver = Callable[[str], list[str]]


class NetworkPolicy:
    def __init__(self, allowed_domains: list[str], resolver: Resolver | None = None) -> None:
        self.allowed_domains = {domain.lower().rstrip(".") for domain in allowed_domains}
        self._resolver = resolver or self._resolve

    def check_url(self, url: str) -> tuple[bool, str, str | None]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https":
            return False, "scheme_blocked", host or None
        if not host or host not in self.allowed_domains:
            return False, "domain_blocked", host or None
        try:
            addresses = self._resolver(host)
        except OSError:
            return False, "dns_resolution_failed", host
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                return False, "ssrf_blocked", host
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
                return False, "ssrf_blocked", host
        return True, "allowed", host

    @staticmethod
    def _resolve(host: str) -> list[str]:
        return sorted({item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})
