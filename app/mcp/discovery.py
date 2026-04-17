"""
mcp/discovery.py — Search public MCP registries and auto-generate server configs.

Searches:
  1. Official MCP Registry (registry.modelcontextprotocol.io) — no auth
  2. Smithery.ai (registry.smithery.ai) — needs SMITHERY_API_KEY

Transforms registry server.json → MCPServerConfig for hot-add to registry.py.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_OFFICIAL_REGISTRY = "https://registry.modelcontextprotocol.io/v0.1"
_SMITHERY_REGISTRY = "https://registry.smithery.ai"
_TIMEOUT = 15.0


@dataclass
class DiscoveredServer:
    """A server found via registry search — not yet installed/connected."""

    name: str
    description: str
    source: str  # "official" or "smithery"
    package_type: str = ""  # "npm", "pip", "docker", ""
    package_id: str = ""  # e.g. "@modelcontextprotocol/server-filesystem"
    runtime_hint: str = ""  # "npx", "uvx", "python", "docker"
    transport: str = "stdio"  # "stdio" or "sse"
    remote_url: str = ""  # for SSE/streamable-http remotes
    env_vars: list[dict] = field(default_factory=list)
    # [{name: "API_KEY", required: True, secret: True, description: "..."}]
    args_schema: list[dict] = field(default_factory=list)
    # [{type: "positional", valueHint: "directory path", required: True}]
    use_count: int = 0
    verified: bool = False
    raw: dict = field(default_factory=dict)

    def to_install_config(self, env_overrides: dict[str, str] | None = None) -> dict:
        """Generate MCPServerConfig-compatible dict for hot-add.

        Args:
            env_overrides: Values for required env vars (e.g. {"API_KEY": "sk-..."}).
        """
        env = {}
        for var in self.env_vars:
            vname = var.get("name", "")
            if not vname:
                continue
            # Check overrides first, then environment, then placeholder
            if env_overrides and vname in env_overrides:
                env[vname] = env_overrides[vname]
            elif os.environ.get(vname):
                env[vname] = os.environ[vname]
            elif var.get("required"):
                env[vname] = f"<SET_{vname}>"

        # Prefer remote URL (no local install needed)
        if self.remote_url:
            return {
                "name": self.name,
                "transport": "sse",
                "url": self.remote_url,
                "env": env,
                "enabled": True,
            }

        # Stdio: build command + args from package info
        cmd = self.runtime_hint or "npx"
        args = []
        if cmd == "npx" and self.package_id:
            args = ["-y", self.package_id]
        elif cmd == "uvx" and self.package_id:
            args = [self.package_id]
        elif cmd in ("python", "python3") and self.package_id:
            args = ["-m", self.package_id]

        return {
            "name": self.name,
            "transport": "stdio",
            "command": cmd,
            "args": args,
            "env": env,
            "enabled": True,
        }

    def format_summary(self) -> str:
        """Human-readable summary for display."""
        parts = [f"**{self.name}**"]
        if self.verified:
            parts[0] += " ✓"
        parts.append(f"  {self.description[:200]}")
        if self.package_id:
            parts.append(f"  Package: {self.package_id} ({self.package_type})")
        if self.remote_url:
            parts.append(f"  Remote: {self.remote_url}")
        if self.env_vars:
            req = [v["name"] for v in self.env_vars if v.get("required")]
            if req:
                parts.append(f"  Requires: {', '.join(req)}")
        if self.use_count:
            parts.append(f"  Used by: {self.use_count:,} installations")
        parts.append(f"  Source: {self.source}")
        return "\n".join(parts)


# ── Official MCP Registry ────────────────────────────────────────────────────


def search_official(query: str, limit: int = 10) -> list[DiscoveredServer]:
    """Search the official MCP registry (no auth required)."""
    try:
        resp = httpx.get(
            f"{_OFFICIAL_REGISTRY}/servers",
            params={"search": query, "version": "latest"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"mcp_discovery: official registry search failed: {e}")
        return []

    results = []
    servers = data.get("servers", [])[:limit]
    for entry in servers:
        if not isinstance(entry, dict):
            continue
        # Official registry wraps each item: {"server": {...actual data...}}
        server = entry.get("server", entry)
        name = server.get("name", "")
        if not name:
            continue

        # Fetch full server.json for package details (has install info)
        detail = _fetch_official_detail(name)
        if detail:
            server = detail

        ds = _parse_official_server(server)
        if ds:
            results.append(ds)
    return results


def _fetch_official_detail(server_name: str) -> dict | None:
    """Fetch full server.json from the official registry."""
    try:
        resp = httpx.get(
            f"{_OFFICIAL_REGISTRY}/servers/{server_name}/versions/latest",
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json().get("server", resp.json())
    except Exception:
        pass
    return None


def _parse_official_server(server: dict) -> DiscoveredServer | None:
    """Parse an official registry server.json into a DiscoveredServer."""
    name = server.get("name", "")
    desc = server.get("description", "")
    if not name:
        return None

    # Extract package info (first package)
    packages = server.get("packages", [])
    pkg = packages[0] if packages else {}
    pkg_type = pkg.get("registryType", "")
    pkg_id = pkg.get("identifier", "")
    runtime = pkg.get("runtimeHint", "npx" if pkg_type == "npm" else "uvx" if pkg_type == "pip" else "")
    transport_info = pkg.get("transport", {})
    transport = transport_info.get("type", "stdio")

    # Environment variables
    env_vars = [
        {
            "name": ev.get("name", ""),
            "required": ev.get("isRequired", False),
            "secret": ev.get("isSecret", False),
            "description": ev.get("description", ""),
        }
        for ev in pkg.get("environmentVariables", [])
    ]

    # Args
    args_schema = pkg.get("packageArguments", [])

    # Remote URLs
    remotes = server.get("remotes", [])
    remote_url = remotes[0].get("url", "") if remotes else ""

    return DiscoveredServer(
        name=name,
        description=desc,
        source="official",
        package_type=pkg_type,
        package_id=pkg_id,
        runtime_hint=runtime,
        transport=transport if not remote_url else "sse",
        remote_url=remote_url,
        env_vars=env_vars,
        args_schema=args_schema,
        raw=server,
    )


# ── Smithery Registry ────────────────────────────────────────────────────────


def search_smithery(query: str, limit: int = 10) -> list[DiscoveredServer]:
    """Search the Smithery registry. Works without API key for basic search."""
    api_key = os.environ.get("SMITHERY_API_KEY", "")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = httpx.get(
            f"{_SMITHERY_REGISTRY}/servers",
            params={"q": query, "pageSize": limit},
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"mcp_discovery: Smithery search failed: {e}")
        return []

    raw_list = data.get("servers", data) if isinstance(data, dict) else []
    if isinstance(raw_list, dict):
        raw_list = [raw_list]

    results = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        ds = _parse_smithery_server(entry)
        if ds:
            results.append(ds)
    return results[:limit]


def _parse_smithery_server(entry: dict) -> DiscoveredServer | None:
    """Parse a Smithery search result into a DiscoveredServer."""
    name = entry.get("qualifiedName", entry.get("name", ""))
    if not name:
        return None

    desc = entry.get("description", "")
    use_count = entry.get("useCount", 0)

    # Smithery provides connection info for remote servers
    remote_url = ""
    if entry.get("remote") or entry.get("isDeployed"):
        remote_url = f"https://server.smithery.ai/{name}/mcp"

    return DiscoveredServer(
        name=name,
        description=desc,
        source="smithery",
        remote_url=remote_url,
        use_count=use_count,
        verified=entry.get("verified", False),
        raw=entry,
    )


# ── Combined Search ──────────────────────────────────────────────────────────


def search_all(query: str, limit: int = 10) -> list[DiscoveredServer]:
    """Search all available registries, deduplicate, and rank by relevance."""
    results: list[DiscoveredServer] = []

    # Search both registries
    results.extend(search_official(query, limit))
    results.extend(search_smithery(query, limit))

    # Deduplicate by name (prefer official over smithery)
    seen: dict[str, DiscoveredServer] = {}
    for ds in results:
        key = ds.name.lower().replace("/", "_").replace("-", "_")
        if key not in seen:
            seen[key] = ds
        elif ds.source == "official" and seen[key].source != "official":
            seen[key] = ds  # prefer official

    # Sort: verified first, then by use_count
    ranked = sorted(
        seen.values(),
        key=lambda d: (not d.verified, -d.use_count, d.name),
    )
    return ranked[:limit]
