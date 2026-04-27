"""Capability detection rules.

Two layers of metadata per capability:
  1. CAPABILITY_RULES — description + risk class (drives the UI).
  2. CAPABILITY_DETECTORS — maps call patterns to a *set of alternatives*
     (any one declared satisfies the detected usage). This handles the
     overlap problem: ``requests.get`` could be HTTP_LAN or HTTP_INTERNET_GET
     depending on runtime address, so either declaration covers it.

The runtime guard (Phase 2) is what disambiguates LAN vs internet at the
moment of the actual syscall.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.forge.manifest import Capability


@dataclass(frozen=True)
class CapabilityRule:
    description: str
    risk_class: str  # "low" | "medium" | "high"


CAPABILITY_RULES: dict[Capability, CapabilityRule] = {
    Capability.HTTP_LAN: CapabilityRule(
        description="HTTP requests to RFC1918 (LAN) addresses only.",
        risk_class="medium",
    ),
    Capability.HTTP_INTERNET_GET: CapabilityRule(
        description="HTTPS GET to public internet (DNS must not resolve to RFC1918).",
        risk_class="medium",
    ),
    Capability.HTTP_INTERNET_POST: CapabilityRule(
        description="HTTPS POST/PUT/PATCH/DELETE to public internet.",
        risk_class="high",
    ),
    Capability.FS_WORKSPACE_READ: CapabilityRule(
        description="Read files under /app/workspace.",
        risk_class="low",
    ),
    Capability.FS_WORKSPACE_WRITE: CapabilityRule(
        description="Write files under /app/workspace/forge/<tool_id>/.",
        risk_class="medium",
    ),
    Capability.EXEC_SANDBOX: CapabilityRule(
        description="Execute code in the existing Docker sandbox via code_executor.",
        risk_class="high",
    ),
    Capability.MCP_CALL: CapabilityRule(
        description="Call a registered MCP server tool (specific server in manifest).",
        risk_class="medium",
    ),
    Capability.SIGNAL_SEND_TO_OWNER: CapabilityRule(
        description="Send a Signal message to the configured owner number only.",
        risk_class="medium",
    ),
}


# Each entry: a call pattern (qualified or bare name) → set of capabilities
# any one of which covers this usage. Detection returns the alternative sets
# rather than concrete capabilities, so the auditor can check
# ``declared ∩ alternatives is non-empty`` per detected pattern.
_HTTP_GET_ALTS = frozenset({
    Capability.HTTP_LAN, Capability.HTTP_INTERNET_GET, Capability.HTTP_INTERNET_POST,
})
_HTTP_POST_ALTS = frozenset({
    Capability.HTTP_LAN, Capability.HTTP_INTERNET_POST,
})
_FS_READ_ALTS = frozenset({
    Capability.FS_WORKSPACE_READ, Capability.FS_WORKSPACE_WRITE,
})
_FS_WRITE_ALTS = frozenset({Capability.FS_WORKSPACE_WRITE})

CAPABILITY_DETECTORS: dict[str, frozenset[Capability]] = {
    # HTTP — GETs are the most permissive end, satisfied by any HTTP cap.
    "requests.get": _HTTP_GET_ALTS,
    "requests.head": _HTTP_GET_ALTS,
    "requests.options": _HTTP_GET_ALTS,
    "httpx.get": _HTTP_GET_ALTS,
    "httpx.head": _HTTP_GET_ALTS,
    "urllib.request.urlopen": _HTTP_GET_ALTS,
    # HTTP — mutating verbs require LAN or explicit POST cap.
    "requests.post": _HTTP_POST_ALTS,
    "requests.put": _HTTP_POST_ALTS,
    "requests.patch": _HTTP_POST_ALTS,
    "requests.delete": _HTTP_POST_ALTS,
    "requests.request": _HTTP_POST_ALTS,
    "httpx.post": _HTTP_POST_ALTS,
    "httpx.put": _HTTP_POST_ALTS,
    "httpx.patch": _HTTP_POST_ALTS,
    "httpx.delete": _HTTP_POST_ALTS,
    "httpx.request": _HTTP_POST_ALTS,
    # FS — bare ``open`` is treated as read; explicit Path.write_text demands write.
    "open": _FS_READ_ALTS,
    "Path.read_text": _FS_READ_ALTS,
    "Path.read_bytes": _FS_READ_ALTS,
    "Path.write_text": _FS_WRITE_ALTS,
    "Path.write_bytes": _FS_WRITE_ALTS,
    "Path.mkdir": _FS_WRITE_ALTS,
    # Sandbox / Signal / MCP
    "execute_code": frozenset({Capability.EXEC_SANDBOX}),
    "signal_client.send": frozenset({Capability.SIGNAL_SEND_TO_OWNER}),
    "SignalClient.send": frozenset({Capability.SIGNAL_SEND_TO_OWNER}),
}


# Imports/calls/attrs that NO forged tool may ever use, regardless of declared
# capabilities. These map to FORBIDDEN_CAPABILITIES classes.
HARD_BLOCKED_IMPORTS: frozenset[str] = frozenset({
    "subprocess", "os.system", "ctypes", "importlib", "pickle", "shelve",
    "marshal", "code", "codeop", "compileall", "pty", "resource", "sysconfig",
    "multiprocessing", "smtplib", "ftplib", "xmlrpc", "http.server",
    "webbrowser", "yaml",
})

HARD_BLOCKED_CALLS: frozenset[str] = frozenset({
    "eval", "exec", "compile", "__import__", "globals", "locals",
    "delattr", "setattr", "type", "breakpoint", "input", "help",
    "os.system", "os.popen", "subprocess.run", "subprocess.Popen",
    "subprocess.call", "subprocess.check_output", "subprocess.check_call",
})

HARD_BLOCKED_ATTRS: frozenset[str] = frozenset({
    "__builtins__", "__subclasses__", "__bases__", "__mro__",
    "__class__", "__globals__", "__code__", "__func__",
    "__self__", "__dict__", "__import__",
})


# Path patterns that no forged tool may touch — touching any of these is
# automatic-reject regardless of how persuasive the LLM judge is.
HARD_BLOCKED_PATH_FRAGMENTS: frozenset[str] = frozenset({
    "/app/forge/", "/app/auto_deployer", "/app/safety_guardian",
    "/app/eval_sandbox", "/app/sanitize", "/app/security",
    "/app/souls/constitution", "/app/config.py", "/app/main.py",
    "/etc/", "/root/", "/.ssh/", "/.aws/",
    "GATEWAY_SECRET", "ANTHROPIC_API_KEY", "BRAVE_API_KEY",
    "OPENROUTER_API_KEY", "MEM0_POSTGRES_PASSWORD",
    "FIREBASE_SERVICE_ACCOUNT", "firebase-service-account",
})


def capability_summary(cap: Capability) -> str:
    rule = CAPABILITY_RULES.get(cap)
    if rule is None:
        return cap.value
    return f"{cap.value} ({rule.risk_class}): {rule.description}"
