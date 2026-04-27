"""Runtime capability guards. Enforce declared capabilities at the moment of I/O.

Static auditing checks declarations are *consistent* with the source. These
guards check the *actual* operation matches the declaration. Both layers are
needed — static catches "you said X but your code does Y", runtime catches
"your code says X but at this address you'd hit Y".

The HTTP guard does DNS resolution to distinguish LAN (RFC1918) from public
internet. Resolution itself is logged so an attacker can't ping arbitrary
internal services via DNS side channels — we record the resolved IP family
in invocation telemetry.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from app.forge.manifest import Capability

logger = logging.getLogger(__name__)


WORKSPACE_ROOT = Path("/app/workspace").resolve()


# ── HTTP guard ──────────────────────────────────────────────────────────────


@dataclass
class HttpDecision:
    allowed: bool
    reason: str
    capability_used: Capability | None = None
    resolved_ip: str | None = None


# Hosts that resolve to *internal* AWS / GCP / Azure metadata services. Even
# if RFC1918 LAN access is granted, these must always be blocked because they
# expose IAM credentials to anyone who can issue an HTTP GET.
_METADATA_BLOCKED_HOSTS: frozenset[str] = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "metadata",
})


def _resolve_first_ip(hostname: str) -> str | None:
    try:
        return socket.gethostbyname(hostname)
    except (socket.gaierror, OSError):
        return None


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
    )


def check_http(
    method: str,
    url: str,
    declared: set[Capability],
    domain_allowlist: list[str],
    blocked_domains: list[str] | None = None,
) -> HttpDecision:
    """Return whether the request is allowed by the declared capability set."""
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return HttpDecision(False, f"malformed URL: {url!r}")
    host = parsed.hostname.lower()
    method_upper = method.upper()

    # Hard-block metadata addresses regardless of declarations.
    if host in _METADATA_BLOCKED_HOSTS:
        return HttpDecision(False, f"blocked metadata host: {host}")

    # Hard-block scheme that isn't http or https.
    if parsed.scheme not in ("http", "https"):
        return HttpDecision(False, f"unsupported scheme: {parsed.scheme}")

    # Domain denylist (if provided)
    for blocked in blocked_domains or []:
        if host == blocked or host.endswith("." + blocked):
            return HttpDecision(False, f"host on global denylist: {host}")

    # If a domain allowlist is set on the manifest, enforce it.
    if domain_allowlist:
        ok = any(
            host == d or host.endswith("." + d)
            for d in domain_allowlist
        )
        if not ok:
            return HttpDecision(False, f"host {host} not in domain allowlist")

    # Resolve to determine LAN vs internet.
    resolved = _resolve_first_ip(host)
    if resolved is None:
        return HttpDecision(False, f"DNS resolution failed for {host}")

    # Resolved metadata address (e.g., a public DNS pointing to 169.254.x.x)
    if resolved in _METADATA_BLOCKED_HOSTS:
        return HttpDecision(False, f"resolved to blocked metadata IP: {resolved}")

    is_private = _is_private_ip(resolved)

    if is_private:
        if Capability.HTTP_LAN not in declared:
            return HttpDecision(
                False,
                f"declared capabilities {sorted(c.value for c in declared)} "
                f"do not include http.lan; refusing to call private IP {resolved}",
                resolved_ip=resolved,
            )
        return HttpDecision(
            True, "LAN call within http.lan",
            capability_used=Capability.HTTP_LAN, resolved_ip=resolved,
        )

    # Public internet path. https only.
    if parsed.scheme != "https":
        return HttpDecision(False, "public internet calls must be HTTPS")

    if method_upper in ("GET", "HEAD", "OPTIONS"):
        if Capability.HTTP_INTERNET_GET in declared:
            return HttpDecision(
                True, "GET allowed by http.internet.https_get",
                capability_used=Capability.HTTP_INTERNET_GET, resolved_ip=resolved,
            )
        if Capability.HTTP_INTERNET_POST in declared:
            return HttpDecision(
                True, "GET allowed by http.internet.https_post (broader)",
                capability_used=Capability.HTTP_INTERNET_POST, resolved_ip=resolved,
            )
        return HttpDecision(
            False, "GET to public internet requires http.internet.https_get",
            resolved_ip=resolved,
        )

    # Mutating verbs require the POST capability explicitly.
    if Capability.HTTP_INTERNET_POST in declared:
        return HttpDecision(
            True, f"{method_upper} allowed by http.internet.https_post",
            capability_used=Capability.HTTP_INTERNET_POST, resolved_ip=resolved,
        )
    return HttpDecision(
        False,
        f"{method_upper} to public internet requires http.internet.https_post",
        resolved_ip=resolved,
    )


# ── Filesystem guard ────────────────────────────────────────────────────────


def check_workspace_path(
    path: str,
    mode: str,  # "read" or "write"
    tool_id: str,
    declared: set[Capability],
) -> tuple[bool, str]:
    """Guard FS access. Read goes anywhere under workspace/. Write is
    restricted to the per-tool directory under workspace/forge/<tool_id>/.
    """
    try:
        target = Path(path).resolve()
    except (OSError, ValueError) as exc:
        return False, f"path resolution failed: {exc}"

    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError:
        return False, f"path outside workspace: {target}"

    if mode == "read":
        if Capability.FS_WORKSPACE_READ in declared or Capability.FS_WORKSPACE_WRITE in declared:
            return True, "workspace read allowed"
        return False, "fs.workspace.read or .write must be declared"

    if mode == "write":
        if Capability.FS_WORKSPACE_WRITE not in declared:
            return False, "fs.workspace.write must be declared"
        per_tool_root = (WORKSPACE_ROOT / "forge" / tool_id).resolve()
        try:
            target.relative_to(per_tool_root)
        except ValueError:
            return False, f"writes restricted to {per_tool_root}"
        return True, "workspace write allowed within per-tool root"

    return False, f"unknown FS mode: {mode}"
