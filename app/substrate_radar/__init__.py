"""substrate_radar — OS / container / cloud EOL tracker.

Tier 2.1 of the 2026-05-24 ultrathink analysis closure.

Sibling to ``app/dependency_radar`` (Python package tracking). This
radar covers the substrate layer:

  * Debian / Ubuntu / Alpine base image EOL dates
  * Docker Compose schema version deprecation
  * Cloud-provider API version sunsets (GCP / AWS) — operator-curated
    list at ``app/substrate_radar/cloud_api_eol.json``
  * Python language version EOL (already covered by §63's
    ``python_eol_proximity`` monitor; we don't duplicate but cross-link)

Master switch: ``substrate_radar_enabled`` (default ON).

Output: structured ``SubstrateFinding`` rows routed to:
  * proposal_bridge for patchable findings
  * Signal alert for non-patchable findings (operator must act)

This is OBSERVATIONAL — like dependency_radar, it never auto-applies.
"""
from __future__ import annotations

from app.substrate_radar.radar import (
    SubstrateFinding,
    SubstrateSeverity,
    detect_findings,
    run_one_pass,
)

__all__ = [
    "SubstrateFinding",
    "SubstrateSeverity",
    "detect_findings",
    "run_one_pass",
]
