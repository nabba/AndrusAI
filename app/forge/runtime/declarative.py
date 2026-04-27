"""Declarative tool interpreter.

A declarative tool is a JSON recipe describing a single HTTP call:

    {
      "method": "GET",
      "url_template": "https://api.example.com/users/{username}",
      "headers": {"Accept": "application/json"},
      "json_body": {...},        # optional, for POST/PUT/PATCH
      "query": {...},            # optional query params
      "timeout_seconds": 10,
      "output_jsonpath": "$"     # dotted/JSON-path applied to response
    }

The interpreter validates parameters against the manifest schema, substitutes
them into the URL template, runs the request through the HTTP capability
guard, executes via ``requests``, and returns the (optionally projected)
response.

This is intentionally minimal — no chained calls, no transformations beyond
JSON-path projection. Anything more complex should be a python_sandbox tool.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from app.forge.manifest import Capability, ToolManifest
from app.forge.runtime.guards import check_http

logger = logging.getLogger(__name__)


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class DeclarativeRuntimeError(Exception):
    """Raised when the recipe is malformed or the call is blocked by guards."""


def _substitute_template(template: str, params: dict[str, Any]) -> str:
    """Replace ``{name}`` placeholders with URL-encoded param values."""
    import urllib.parse

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in params:
            raise DeclarativeRuntimeError(f"missing template parameter: {key}")
        return urllib.parse.quote(str(params[key]), safe="")

    return _PLACEHOLDER_RE.sub(_sub, template)


def _project(data: Any, path: str | None) -> Any:
    """Tiny dotted-path projector. ``$`` returns root. ``foo.bar.0`` walks in."""
    if not path or path.strip() == "$":
        return data
    cur = data
    for part in path.split("."):
        if part == "$":
            continue
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _validate_params(params: dict[str, Any], schema: dict[str, Any]) -> None:
    """Lightweight schema check — type only, no deep validation."""
    if not schema:
        return
    for name, spec in schema.items():
        if name not in params:
            continue  # treat missing as optional unless explicitly required
        expected = (spec or {}).get("type") if isinstance(spec, dict) else None
        if not expected:
            continue
        val = params[name]
        ok = (
            (expected == "string" and isinstance(val, str))
            or (expected == "number" and isinstance(val, (int, float)))
            or (expected == "integer" and isinstance(val, int))
            or (expected == "boolean" and isinstance(val, bool))
            or (expected == "object" and isinstance(val, dict))
            or (expected == "array" and isinstance(val, list))
        )
        if not ok:
            raise DeclarativeRuntimeError(
                f"parameter {name!r} expected {expected}, got {type(val).__name__}"
            )


def run_declarative(
    manifest: ToolManifest,
    source_code: str,
    params: dict[str, Any],
    blocked_domains: list[str] | None = None,
    request_timeout_ceiling: float = 30.0,
) -> dict[str, Any]:
    """Execute a declarative tool recipe. Returns a structured invocation result.

    Output dict shape:
      {
        "ok": bool,
        "status_code": int | None,
        "result": Any,            # projected response when ok
        "error": str | None,
        "capability_used": str | None,
        "resolved_ip": str | None,
        "elapsed_ms": int,
      }
    """
    try:
        recipe = json.loads(source_code)
    except json.JSONDecodeError as exc:
        raise DeclarativeRuntimeError(f"recipe is not valid JSON: {exc}") from exc
    if not isinstance(recipe, dict):
        raise DeclarativeRuntimeError("recipe must be a JSON object")

    method = str(recipe.get("method", "GET")).upper()
    url_template = recipe.get("url_template") or recipe.get("url")
    if not url_template:
        raise DeclarativeRuntimeError("recipe missing 'url_template' or 'url'")

    headers = recipe.get("headers") or {}
    json_body = recipe.get("json_body")
    query = recipe.get("query") or {}
    timeout = float(recipe.get("timeout_seconds", 10.0))
    timeout = min(timeout, request_timeout_ceiling)
    output_path = recipe.get("output_jsonpath", "$")

    _validate_params(params, manifest.parameters or {})

    url = _substitute_template(str(url_template), params)
    # Allow query param values to use template substitution too
    resolved_query: dict[str, Any] = {}
    for k, v in query.items():
        if isinstance(v, str):
            resolved_query[k] = _substitute_template(v, params)
        else:
            resolved_query[k] = v

    declared = {
        Capability(c) if not isinstance(c, Capability) else c
        for c in manifest.capabilities
    }
    decision = check_http(
        method=method,
        url=url,
        declared=declared,
        domain_allowlist=manifest.domain_allowlist,
        blocked_domains=blocked_domains or [],
    )
    if not decision.allowed:
        return {
            "ok": False,
            "status_code": None,
            "result": None,
            "error": f"capability guard refused: {decision.reason}",
            "capability_used": None,
            "resolved_ip": decision.resolved_ip,
            "elapsed_ms": 0,
            "violation": decision.reason,
        }

    # Lazy import — keep forge runtime import-cheap.
    import requests

    start = time.monotonic()
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=resolved_query or None,
            json=json_body if method in ("POST", "PUT", "PATCH") else None,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status_code": None,
            "result": None,
            "error": f"http error: {type(exc).__name__}: {exc}",
            "capability_used": decision.capability_used.value if decision.capability_used else None,
            "resolved_ip": decision.resolved_ip,
            "elapsed_ms": int((time.monotonic() - start) * 1000),
        }

    elapsed_ms = int((time.monotonic() - start) * 1000)
    body: Any
    try:
        body = response.json()
    except ValueError:
        body = response.text[:8192]
    projected = _project(body, output_path)

    return {
        "ok": response.ok,
        "status_code": response.status_code,
        "result": projected,
        "error": None if response.ok else f"http {response.status_code}",
        "capability_used": (
            decision.capability_used.value if decision.capability_used else None
        ),
        "resolved_ip": decision.resolved_ip,
        "elapsed_ms": elapsed_ms,
    }
