"""mcp_discovery — weekly poll of the MCP registry.

Tier 2.3 of the 2026-05-24 ultrathink analysis closure.

The MCP ecosystem grows continuously. New high-rated connectors
(Atlassian, Figma, Kubernetes, PDF tooling, etc.) ship every week.
This module is the system's antenna: scans the registry, dedupes
against already-integrated servers, files proposal_bridge entries
for high-rated novel servers so the operator can evaluate.

Trust posture
=============

  * Discovery is observational — never integrates untrusted code.
  * Operator approval REQUIRED for every adoption (standard
    change_requests gate).
  * Curated denylist at ``workspace/mcp_discovery/denylist.txt`` lets
    the operator pin specific connectors out.
  * Quality filter: minimum_rating + minimum_install_count gates
    before a candidate even reaches proposal_bridge.

Master switch: ``mcp_discovery_enabled`` (default OFF — security-
sensitive surface, opt-in only).
"""
from __future__ import annotations

from app.mcp_discovery.poller import (
    DiscoveredConnector,
    run_discovery_pass,
)

__all__ = [
    "DiscoveredConnector",
    "run_discovery_pass",
]
