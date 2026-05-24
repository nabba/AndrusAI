"""config_coherence — Detects incoherent or orphaned runtime_settings combinations.

Gap #3 (2026-05-24): with ~80 master switches accumulated across Q1-Q18 +
§55-§70, conflicting combinations can produce silent dysfunction. A few
canonical examples:

    interest_goal_emitter ON + autonomous_executor OFF
        → goals emit, never run.

    person_centrality_enabled ON + person_correlation_enabled OFF
        → centrality formula has no data.

    structured_diagnosis floor > ceiling
        → auto-tuner pinned at the inverted bound.

    binauthz_mode ENFORCE + binauthz_attestor_name empty
        → ENFORCE silently falls back to ALWAYS_ALLOW (worst-of-both: the
        operator believes signing is enforced, but it isn't).

This monitor walks a curated rule list weekly, files a Signal alert with
the consolidated set of findings + resolution hints. Observational only —
never flips a switch on its own. Operator approves the proposed
resolution via standard React /cp/settings flips (which now get recorded
in the settings genealogy ledger from Gap #4, closing the audit loop).

Cadence
=======

Daily probe, internal weekly cadence inside the monitor itself.
Per-rule-id 28-day Signal dedup so a long-running misconfiguration
doesn't spam.

What this is NOT
================

  * Not a tier-3 dependency checker. Tier-3 amendments have their own
    eligibility gate.
  * Not a security scanner. The rules here are correctness invariants,
    not threat-model checks.
  * Not auto-fixing. Every finding ships with a resolution hint; the
    operator runs the hint via the standard settings card.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


NAME = "config_coherence"
CADENCE_SECONDS = 24 * 3600
MASTER_SWITCH_KEY = "config_coherence_monitor_enabled"

_INTERNAL_CADENCE_S = 7 * 24 * 3600
_DEDUP_WINDOW_S = 28 * 86400
_STATE_FILE_NAME = "config_coherence_state.json"


def _enabled() -> bool:
    try:
        from app.runtime_settings import _ensure_initialized
        return bool(_ensure_initialized().get(MASTER_SWITCH_KEY, True))
    except Exception:
        return os.getenv(
            "CONFIG_COHERENCE_MONITOR_ENABLED", "true",
        ).lower() in ("true", "1", "yes", "on")


def _workspace() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _state_path() -> Path:
    return _workspace() / "healing" / _STATE_FILE_NAME


def _read_state() -> dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"last_run_at": 0.0, "last_alert_at": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"last_run_at": 0.0, "last_alert_at": {}}


def _write_state(state: dict[str, Any]) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8",
        )
    except Exception:
        logger.debug("config_coherence: state write failed", exc_info=True)


# ── Finding shape ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str  # info | warning | critical
    title: str
    detail: str
    resolution: str


# ── Rules ────────────────────────────────────────────────────────────────
#
# Each rule reads the snapshot dict + returns a Finding when the
# combination is incoherent. None when the combination is fine. The
# rule_id is stable for dedup; severity drives alert grouping. Resolution
# strings are the literal setting flips the operator should perform.


def _b(snapshot: dict[str, Any], key: str, default: bool = False) -> bool:
    """Read a boolean key tolerantly — missing/None falls back to default
    rather than raising. Some keys are only present on first-flip."""
    v = snapshot.get(key, default)
    return bool(v) if v is not None else default


def _rule_goodhart_disabled_and_enforcing(s: dict[str, Any]) -> Optional[Finding]:
    if _b(s, "goodhart_hard_gate_disabled") and _b(s, "goodhart_hard_gate_enforcing"):
        return Finding(
            rule_id="goodhart_disabled_and_enforcing",
            severity="warning",
            title="Goodhart hard gate is disabled AND enforcing",
            detail=(
                "goodhart_hard_gate_disabled=True overrides the enforcing flag "
                "entirely. The gate is OFF; the enforcing setting has no effect."
            ),
            resolution=(
                "If you want enforcement: set goodhart_hard_gate_disabled=False "
                "AND goodhart_hard_gate_enforcing=True. If you want the gate off: "
                "clear goodhart_hard_gate_enforcing=False so the intent is "
                "consistent."
            ),
        )
    return None


def _rule_structured_diagnosis_band_inverted(s: dict[str, Any]) -> Optional[Finding]:
    floor = s.get("structured_diagnosis_threshold_floor")
    ceiling = s.get("structured_diagnosis_threshold_ceiling")
    if isinstance(floor, (int, float)) and isinstance(ceiling, (int, float)):
        if floor > ceiling:
            return Finding(
                rule_id="structured_diagnosis_band_inverted",
                severity="critical",
                title="Structured-diagnosis threshold band is inverted",
                detail=(
                    f"floor={floor} > ceiling={ceiling}. The auto-tuner clamps "
                    "the active threshold to the band, so an inverted band pins "
                    "the threshold at the inversion point and breaks tuning."
                ),
                resolution=(
                    "Set structured_diagnosis_threshold_floor ≤ "
                    "structured_diagnosis_threshold_ceiling (canonical defaults: "
                    "0.50 floor, 0.95 ceiling)."
                ),
            )
    return None


def _rule_structured_diagnosis_override_out_of_band(s: dict[str, Any]) -> Optional[Finding]:
    floor = s.get("structured_diagnosis_threshold_floor")
    ceiling = s.get("structured_diagnosis_threshold_ceiling")
    override = s.get("structured_diagnosis_threshold_override")
    if override is None:
        return None
    if not (isinstance(floor, (int, float)) and isinstance(ceiling, (int, float))):
        return None
    if not isinstance(override, (int, float)):
        return None
    if override < floor or override > ceiling:
        return Finding(
            rule_id="structured_diagnosis_override_out_of_band",
            severity="warning",
            title="Structured-diagnosis override is outside the operator band",
            detail=(
                f"override={override} but band is [{floor}, {ceiling}]. The "
                "override silently wins over the band, so the band's purpose "
                "as a safety guardrail is bypassed."
            ),
            resolution=(
                "Either move the override into the band, widen the band to "
                "include the override, or clear the override (set "
                "structured_diagnosis_threshold_override=null)."
            ),
        )
    return None


def _rule_vision_cu_enabled_with_zero_cap(s: dict[str, Any]) -> Optional[Finding]:
    if _b(s, "vision_cu_enabled"):
        cap = s.get("vision_cu_monthly_cap_usd")
        if isinstance(cap, (int, float)) and cap <= 0:
            return Finding(
                rule_id="vision_cu_enabled_with_zero_cap",
                severity="warning",
                title="Vision CU enabled but monthly cap is zero",
                detail=(
                    f"vision_cu_enabled=True with vision_cu_monthly_cap_usd={cap}. "
                    "Every CU invocation will be refused for budget exhaustion — "
                    "the feature is enabled in name only."
                ),
                resolution=(
                    "Set vision_cu_monthly_cap_usd to a non-zero budget, or "
                    "set vision_cu_enabled=False if you intend to keep CU off."
                ),
            )
    return None


def _rule_binauthz_enforce_without_attestor(s: dict[str, Any]) -> Optional[Finding]:
    mode = str(s.get("binauthz_mode", "")).upper()
    attestor = str(s.get("binauthz_attestor_name", "")).strip()
    if mode == "ENFORCE" and not attestor:
        return Finding(
            rule_id="binauthz_enforce_without_attestor",
            severity="critical",
            title="Binary Authorization ENFORCE with no attestor wired",
            detail=(
                "binauthz_mode=ENFORCE but binauthz_attestor_name is empty. GKE "
                "Binary Authorization silently falls back to ALWAYS_ALLOW when "
                "there is no attestor — so unsigned images deploy successfully "
                "while you believe signing is enforced. Worst-of-both."
            ),
            resolution=(
                "Run scripts/install/cosign_setup.sh to provision an attestor, "
                "then set binauthz_attestor_name to that attestor's short name. "
                "Alternative: keep binauthz_mode=AUDIT until the attestor is "
                "ready."
            ),
        )
    return None


def _rule_person_centrality_without_correlation(s: dict[str, Any]) -> Optional[Finding]:
    if _b(s, "person_centrality_enabled") and not _b(s, "person_correlation_enabled"):
        return Finding(
            rule_id="person_centrality_without_correlation",
            severity="warning",
            title="Person centrality enabled without person correlation",
            detail=(
                "person_centrality_enabled=True needs person_correlation_enabled=True "
                "to have any data to compute centrality over. As-is, every centrality "
                "score will be zero."
            ),
            resolution=(
                "Enable person_correlation_enabled before person_centrality_enabled "
                "(progressive opt-in order: L1 → L2 → L3 → L4)."
            ),
        )
    return None


def _rule_person_suggestions_without_correlation(s: dict[str, Any]) -> Optional[Finding]:
    if _b(s, "person_suggestions_enabled") and not _b(s, "person_correlation_enabled"):
        return Finding(
            rule_id="person_suggestions_without_correlation",
            severity="warning",
            title="Person suggestions enabled without person correlation",
            detail=(
                "person_suggestions_enabled=True needs L1 person_correlation_enabled "
                "to have a presence model to suggest from."
            ),
            resolution="Enable person_correlation_enabled first.",
        )
    return None


def _rule_graph_feature_without_social_graph(s: dict[str, Any]) -> Optional[Finding]:
    if not _b(s, "person_correlation_social_graph_enabled"):
        leaves = [
            "graph_shortest_path_enabled",
            "graph_communities_enabled",
            "graph_bridges_enabled",
            "graph_suggestions_enabled",
        ]
        on_leaves = [k for k in leaves if _b(s, k)]
        if on_leaves:
            return Finding(
                rule_id="graph_feature_without_social_graph",
                severity="warning",
                title="Graph features enabled without the L4 social-graph parent",
                detail=(
                    f"{', '.join(on_leaves)} enabled but person_correlation_social_graph_enabled=False. "
                    "L4 features need the social-graph base to be enabled."
                ),
                resolution=(
                    "Enable person_correlation_social_graph_enabled (requires the "
                    "L4 typed-phrase confirmation), or disable the leaf features."
                ),
            )
    return None


def _rule_graph_suggestions_sub_without_parent(s: dict[str, Any]) -> Optional[Finding]:
    if not _b(s, "graph_suggestions_enabled"):
        leaves = [
            "graph_suggestions_cluster_dormancy_enabled",
            "graph_suggestions_bridge_maintenance_enabled",
            "graph_suggestions_weak_tie_enabled",
        ]
        on_leaves = [k for k in leaves if _b(s, k)]
        if on_leaves:
            return Finding(
                rule_id="graph_suggestions_sub_without_parent",
                severity="warning",
                title="Graph-suggestion sub-features without the L4.4 parent",
                detail=(
                    f"{', '.join(on_leaves)} enabled but graph_suggestions_enabled=False. "
                    "The L4.4 parent must be on (typed-phrase gated) for any sub-feature "
                    "to emit."
                ),
                resolution="Enable graph_suggestions_enabled or disable the sub-features.",
            )
    return None


def _rule_drills_disabled_with_per_drill_on(s: dict[str, Any]) -> Optional[Finding]:
    if _b(s, "resilience_drills_enabled", default=True):
        return None
    per_drill_keys = [
        "drill_backup_restore_enabled",
        "drill_embedding_migration_enabled",
        "drill_secret_rotation_enabled",
        "drill_kill_the_gateway_enabled",
        "drill_task_recovery_enabled",
        "drill_vendor_independence_enabled",
        "drill_local_only_enabled",
        "drill_source_ledger_replay_enabled",
        "drill_embedding_rotation_enabled",
        "drill_fresh_host_bootstrap_enabled",
        "drill_prompt_injection_resistance_enabled",
    ]
    on_drills = [k for k in per_drill_keys if _b(s, k)]
    if on_drills:
        return Finding(
            rule_id="drills_disabled_with_per_drill_on",
            severity="info",
            title="Master resilience-drills switch is OFF but per-drill toggles are ON",
            detail=(
                f"{len(on_drills)} per-drill toggle(s) are ON but resilience_drills_enabled=False "
                "blocks the scheduler from running any of them. The toggles are cosmetic until "
                "the master is restored."
            ),
            resolution="Enable resilience_drills_enabled, or disable the per-drill toggles for consistency.",
        )
    return None


def _rule_upgrade_lifecycle_sub_without_parent(s: dict[str, Any]) -> Optional[Finding]:
    if _b(s, "upgrade_lifecycle_enabled", default=True):
        return None
    sub_keys = [
        "upgrade_lifecycle_capability_extraction_enabled",
        "upgrade_lifecycle_trial_enabled",
        "upgrade_lifecycle_major_auto_cr_enabled",
        "upgrade_lifecycle_capability_adoption_enabled",
        "upgrade_lifecycle_apply_hook_enabled",
        "upgrade_lifecycle_requirements_writer_enabled",
        "upgrade_lifecycle_dockerfile_writer_enabled",
        "upgrade_lifecycle_pyproject_writer_enabled",
        "upgrade_lifecycle_absence_policy_enabled",
        "ecosystem_snapshot_enabled",
    ]
    on_subs = [k for k in sub_keys if _b(s, k)]
    if on_subs:
        return Finding(
            rule_id="upgrade_lifecycle_sub_without_parent",
            severity="info",
            title="Upgrade-lifecycle sub-features enabled but master is OFF",
            detail=(
                f"{', '.join(on_subs[:3])}{'…' if len(on_subs) > 3 else ''} enabled "
                "but upgrade_lifecycle_enabled=False. Sub-feature toggles have no "
                "effect until the master is restored."
            ),
            resolution="Restore upgrade_lifecycle_enabled=True, or disable the sub-toggles.",
        )
    return None


def _rule_apply_hook_without_any_writer(s: dict[str, Any]) -> Optional[Finding]:
    if not _b(s, "upgrade_lifecycle_apply_hook_enabled"):
        return None
    writers = [
        "upgrade_lifecycle_requirements_writer_enabled",
        "upgrade_lifecycle_dockerfile_writer_enabled",
        "upgrade_lifecycle_pyproject_writer_enabled",
    ]
    if not any(_b(s, k) for k in writers):
        return Finding(
            rule_id="apply_hook_without_any_writer",
            severity="warning",
            title="Upgrade apply-hook enabled with no writer enabled",
            detail=(
                "upgrade_lifecycle_apply_hook_enabled=True but none of the three writers "
                "(requirements / dockerfile / pyproject) are on. Approved CRs will be "
                "detected by the hook and silently dropped — no diff is producible "
                "without a writer."
            ),
            resolution=(
                "Enable at least one of "
                "upgrade_lifecycle_requirements_writer_enabled / "
                "upgrade_lifecycle_dockerfile_writer_enabled / "
                "upgrade_lifecycle_pyproject_writer_enabled."
            ),
        )
    return None


def _rule_architecture_adoption_without_requests(s: dict[str, Any]) -> Optional[Finding]:
    adopt = _b(s, "architecture_adoption_monitor_enabled", default=True)
    requests = _b(s, "architecture_requests_enabled", default=True)
    if adopt and not requests:
        return Finding(
            rule_id="architecture_adoption_without_requests",
            severity="warning",
            title="Architecture-adoption monitor on, architecture-requests subsystem off",
            detail=(
                "architecture_adoption_monitor_enabled=True but architecture_requests_enabled=False. "
                "The adoption monitor scans architecture_requests for low-signal applied items; with "
                "the subsystem off, the monitor's input set is empty and the probe is a no-op."
            ),
            resolution="Restore architecture_requests_enabled=True, or disable the adoption monitor.",
        )
    return None


def _rule_chat_blocklist_runaway(s: dict[str, Any]) -> Optional[Finding]:
    blocklist = s.get("chat_blocked_models")
    no_fc = s.get("no_function_calling_models")
    total = 0
    if isinstance(blocklist, list):
        total += len(blocklist)
    if isinstance(no_fc, list):
        total += len(no_fc)
    if total > 50:
        return Finding(
            rule_id="chat_blocklist_runaway",
            severity="warning",
            title="Model capability blocklists have grown large",
            detail=(
                f"chat_blocked_models + no_function_calling_models = {total} entries. "
                "These lists are populated by self-heal handlers; runaway growth suggests "
                "either a single vendor producing many transient failures, or stale entries "
                "for retired models that should be pruned."
            ),
            resolution=(
                "Inspect the lists via GET /api/cp/settings and trim entries for vendors "
                "you've rotated off. Set chat_blocked_models=[] and/or "
                "no_function_calling_models=[] to reset entirely."
            ),
        )
    return None


def _rule_vpc_sc_live_first_time(s: dict[str, Any]) -> Optional[Finding]:
    if _b(s, "vpc_sc_enabled") and not _b(s, "vpc_sc_dry_run", default=True):
        return Finding(
            rule_id="vpc_sc_live_no_dry_run",
            severity="info",
            title="VPC-SC enabled with dry-run OFF",
            detail=(
                "vpc_sc_enabled=True with vpc_sc_dry_run=False means the next apply "
                "produces live perimeter policies. Misconfiguration can lock the "
                "operator out of their own buckets."
            ),
            resolution=(
                "If this is intentional and you've verified the perimeter via a "
                "dry-run apply, no action is needed. If not, flip vpc_sc_dry_run=True."
            ),
        )
    return None


_RULES: list[Callable[[dict[str, Any]], Optional[Finding]]] = [
    _rule_goodhart_disabled_and_enforcing,
    _rule_structured_diagnosis_band_inverted,
    _rule_structured_diagnosis_override_out_of_band,
    _rule_vision_cu_enabled_with_zero_cap,
    _rule_binauthz_enforce_without_attestor,
    _rule_person_centrality_without_correlation,
    _rule_person_suggestions_without_correlation,
    _rule_graph_feature_without_social_graph,
    _rule_graph_suggestions_sub_without_parent,
    _rule_drills_disabled_with_per_drill_on,
    _rule_upgrade_lifecycle_sub_without_parent,
    _rule_apply_hook_without_any_writer,
    _rule_architecture_adoption_without_requests,
    _rule_chat_blocklist_runaway,
    _rule_vpc_sc_live_first_time,
]


def evaluate(snapshot: dict[str, Any]) -> list[Finding]:
    """Walk all rules; return findings in declaration order."""
    findings: list[Finding] = []
    for rule in _RULES:
        try:
            result = rule(snapshot)
        except Exception:
            logger.debug("config_coherence: rule raised", exc_info=True)
            continue
        if result is not None:
            findings.append(result)
    return findings


def _format_alert_body(findings: list[Finding]) -> str:
    icon = {"critical": "🔴", "warning": "🟠", "info": "🔵"}
    lines = [
        f"⚙️ Settings coherence findings: {len(findings)} issue(s) to review.",
        "",
    ]
    for f in findings:
        lines.append(f"{icon.get(f.severity, '·')} **{f.title}** ({f.rule_id})")
        lines.append(f"   {f.detail}")
        lines.append(f"   → {f.resolution}")
        lines.append("")
    lines.append("Resolve via /cp/settings; each flip is recorded in the settings genealogy ledger.")
    return "\n".join(lines)


def _emit_alert_if_due(
    state: dict[str, Any],
    findings: list[Finding],
    *,
    now: float,
) -> bool:
    """One Signal alert per pass containing all findings.

    Per-rule-id dedup window: a rule that fires every week shouldn't
    spam the operator. We dedup on the *set* of rule_ids that fired —
    a stable misconfiguration produces a stable set, so the dedup
    window covers it. A new finding joining the set re-fires.
    """
    if not findings:
        return False
    last_alerts = state.setdefault("last_alert_at", {})
    if not isinstance(last_alerts, dict):
        last_alerts = {}
        state["last_alert_at"] = last_alerts
    set_key = ",".join(sorted(f.rule_id for f in findings))
    last = float(last_alerts.get(set_key, 0))
    if now - last < _DEDUP_WINDOW_S:
        return False
    last_alerts[set_key] = now
    # Trim the dedup map to prevent unbounded growth (a churning set
    # would otherwise leave a permanent breadcrumb per unique
    # combination).
    if len(last_alerts) > 64:
        oldest = sorted(last_alerts.items(), key=lambda kv: kv[1])
        for k, _ in oldest[:32]:
            last_alerts.pop(k, None)

    has_critical = any(f.severity == "critical" for f in findings)
    title_prefix = "🔴" if has_critical else "⚙️"
    try:
        from app.notify import notify
        notify(
            title=f"{title_prefix} Settings coherence: {len(findings)} issue(s)",
            body=_format_alert_body(findings),
            url="/cp/settings",
            topic=f"config_coherence:{set_key[:64]}",
            critical=has_critical,
            arbitrate=True,
        )
        return True
    except Exception:
        logger.debug("config_coherence: notify failed", exc_info=True)
        return False


def run(*, now: Optional[float] = None) -> dict[str, Any]:
    """One probe pass. Daily wake-up; gated to weekly internal cadence."""
    if not _enabled():
        return {"ran": False, "skipped": True}

    cur = float(now) if now is not None else time.time()
    state = _read_state()
    last = float(state.get("last_run_at", 0))
    if last > 0 and cur - last < _INTERNAL_CADENCE_S:
        return {"ran": False}

    state["last_run_at"] = cur

    try:
        from app.runtime_settings import snapshot
        snap = snapshot()
    except Exception:
        logger.warning("config_coherence: runtime_settings unreadable", exc_info=True)
        _write_state(state)
        return {"ran": True, "findings": [], "error": "runtime_settings_unreadable"}

    findings = evaluate(snap)
    alert_sent = _emit_alert_if_due(state, findings, now=cur)
    _write_state(state)

    return {
        "ran": True,
        "iso": datetime.fromtimestamp(cur, tz=timezone.utc).isoformat(),
        "n_findings": len(findings),
        "findings": [asdict(f) for f in findings],
        "alert_sent": alert_sent,
    }


__all__ = ["run", "evaluate", "Finding"]
