"""SSRF guard and webhook URL destination validation service.

Owns URL parsing, scheme checking, IP range verification, and DNS address resolution checks.
Must NOT issue HTTP requests, store database entities, or log sensitive user information.
"""
import ipaddress
import socket
from typing import Callable, List, Optional, Union
import urllib.parse

from app.config import NOTIFY_ALLOW_HTTP_WEBHOOK

__all__ = ["validate_webhook_url", "is_disallowed_ip"]


def is_disallowed_ip(ip_obj: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    """Returns True if the IP is loopback, private, link-local, multicast, reserved, or unspecified."""
    # Check IPv4-mapped IPv6 addresses (e.g., ::ffff:127.0.0.1)
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        return is_disallowed_ip(ip_obj.ipv4_mapped)

    if ip_obj.is_loopback:
        return True
    if ip_obj.is_private:
        return True
    if ip_obj.is_link_local:
        return True
    if ip_obj.is_multicast:
        return True
    if ip_obj.is_reserved:
        return True
    if ip_obj.is_unspecified:
        return True
    return False


def _default_resolver(host: str) -> List[str]:
    """Default DNS resolver using standard socket library."""
    try:
        addr_info = socket.getaddrinfo(host, None)
        # addr_info is a list of (family, type, proto, canonname, sockaddr)
        # sockaddr[0] is the IP address string
        resolved = []
        for item in addr_info:
            ip_str = item[4][0]
            if ip_str not in resolved:
                resolved.append(ip_str)
        return resolved
    except Exception as exc:
        raise ValueError(f"Could not resolve host '{host}': {exc}") from exc


def validate_webhook_url(
    url: str,
    resolver: Optional[Callable[[str], List[str]]] = None,
) -> None:
    """Validates destination webhook URL against scheme requirements, userinfo leaks, and SSRF ranges.

    Raises:
        ValueError if the URL is invalid, insecure, contains userinfo, or targets non-public IP addresses.
    """
    if not url or not isinstance(url, str):
        raise ValueError("Webhook URL must be a non-empty string.")

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ValueError(f"Invalid webhook URL format: {exc}") from exc

    # 1. Scheme check
    allowed_schemes = ("https",) if not NOTIFY_ALLOW_HTTP_WEBHOOK else ("https", "http")
    if parsed.scheme.lower() not in allowed_schemes:
        raise ValueError(
            f"Invalid URL scheme '{parsed.scheme}'. Scheme must be HTTPS"
            + (" (or HTTP in development)" if NOTIFY_ALLOW_HTTP_WEBHOOK else "")
            + "."
        )

    # 2. Userinfo check
    if parsed.username or parsed.password or ("@" in (parsed.netloc or "")):
        raise ValueError("Webhook URL must not contain user credentials (userinfo).")

    host = parsed.hostname
    if not host or not host.strip():
        raise ValueError("Webhook URL missing valid hostname.")

    host = host.strip().lower()

    # Reject explicit localhost literal
    if host == "localhost":
        raise ValueError("Webhook target 'localhost' is disallowed.")

    # 3. Check if host is a raw IP literal
    try:
        raw_ip = ipaddress.ip_address(host)
        if is_disallowed_ip(raw_ip):
            raise ValueError("Webhook target IP address is disallowed (private/internal/reserved).")
        return
    except ValueError as ve:
        # If ve is our own raised error, bubble it up
        if "Webhook target IP address is disallowed" in str(ve):
            raise
        # Not a raw IP literal, proceed to DNS resolution

    # 4. Resolve hostname
    resolve_fn = resolver or _default_resolver
    resolved_ips = resolve_fn(host)
    if not resolved_ips:
        raise ValueError(f"Hostname '{host}' resolved to no IP addresses.")

    # 5. EVERY resolved IP must be public
    for ip_str in resolved_ips:
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise ValueError(f"Resolved address '{ip_str}' is not a valid IP: {exc}") from exc

        if is_disallowed_ip(ip_obj):
            raise ValueError(
                "Webhook destination resolves to a private, loopback, or reserved IP address."
            )
