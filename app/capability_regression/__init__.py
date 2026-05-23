"""Capability-regression alert subsystem.

Snapshots the set of registered tools + effective LLM-catalog models on
an hourly cadence; alerts on SHRINK (tool unregistered, model removed
from catalog) so the operator notices if a refactor or registry refresh
silently dropped agent capabilities.

Three-state distinction in the report:

  * tools_deleted        — registered tools that vanished (regression)
  * models_truly_deleted — catalog entries that vanished entirely
                            (regression — provider sunset, catalog
                            rebuild lost the entry, etc.)
  * models_newly_blocked — models that moved from "effective" to
                            "blocked" via runtime_settings — informational
                            only; the operator caused it.

Additions are silent — capability GROWTH is welcome and not a signal.

Default ON (fail-open observability). Master switch:
``runtime_settings.capability_regression_enabled``.

Reads only — never modifies the tool registry or LLM catalog.
"""

from app.capability_regression.detector import (
    RegressionReport,
    detect_regressions,
)
from app.capability_regression.snapshot import (
    CapabilitySnapshot,
    load_snapshot,
    save_snapshot,
    take_snapshot,
)

__all__ = [
    "CapabilitySnapshot",
    "RegressionReport",
    "detect_regressions",
    "load_snapshot",
    "save_snapshot",
    "take_snapshot",
]
