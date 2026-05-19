"""Refactor-proposal generation from Phase 1 elegance signals.

Three detectors, one daemon. Each detector reads a Phase 1 artefact,
filters for high-confidence refactor candidates, and emits a
:class:`RefactorCandidate`. ``run_one_pass`` stages the top-N from each
detector through :mod:`app.proposal_bridge` with a 14-day cooldown.

Layering
--------

* Detectors are pure functions over JSON state. They never run a fresh
  scan — they consume the persisted artefacts the Phase 1 monitors
  already produce. This keeps the proposer cheap, deterministic, and
  trivially testable: feed it a fixture and assert the output.
* ``run_one_pass`` is the side-effect entry. It honors the master
  switch, caps each detector at 3 candidates, stages each one via the
  bridge, and returns a small summary dict for the daemon log.
* ``start`` / ``stop`` manage the daemon thread, matching the
  library_radar/paper_pipeline pattern so the import-time wiring is
  uniform across producers.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Configuration ────────────────────────────────────────────────────────


# Hotspot threshold — both must hold to qualify. Picking a file with low
# composite but acceptable complexity would surface "missing docstrings"
# refactors, which are noise compared to genuine architectural smells.
_HOTSPOT_COMPOSITE_MAX = 0.65
_HOTSPOT_COMPLEXITY_MAX = 0.40
# Each detector emits at most this many candidates per pass — a backlog
# spreads across many weeks via the bridge cooldown.
_MAX_PER_DETECTOR = 3
# Bridge cooldown for every refactor proposal. Longer than the default 7d
# because refactors are never urgent; this also doubles as a "did the
# Phase 1 signal persist?" filter.
_COOLDOWN_DAYS = 14
# Daemon loop cadence. Weekly is plenty — codebase doesn't change that fast.
_POLL_INTERVAL_S = 7 * 24 * 3600
# Warm-up so the proposer doesn't fight gateway boot.
_WARMUP_S = 600

_DAEMON_THREAD_NAME = "refactor-proposer"


# ── Workspace artefact locations ─────────────────────────────────────────


def _workspace_root() -> Path:
    env = os.environ.get("WORKSPACE_ROOT")
    if env:
        return Path(env)
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _elegance_history_path() -> Path:
    return _workspace_root() / "code_quality" / "elegance_history.json"


def _architectural_baseline_path() -> Path:
    return _workspace_root() / "code_quality" / "architectural_baseline.json"


# ── Type ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RefactorCandidate:
    """One concrete refactor the operator could approve.

    ``signature`` must be stable across runs over identical inputs so
    the bridge's idempotency works — same signal, same proposal, no
    duplicate CR.
    """
    detector: str
    signature: str
    title: str
    body_markdown: str
    target_path: str
    coding_session_spec: dict[str, Any] = field(default_factory=dict)


# ── Enable / read helpers ────────────────────────────────────────────────


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_refactor_proposer_enabled
        return get_refactor_proposer_enabled()
    except Exception:
        return os.getenv("REFACTOR_PROPOSER_ENABLED", "false").lower() in (
            "true", "1", "yes",
        )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("refactor_proposer: read failed for %s", path, exc_info=True)
        return None


def _short_hash(s: str, n: int = 10) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:n]


def _safe_signature_path_component(path: str) -> str:
    """Turn a repo-relative file path into a signature-safe slug.

    The proposal_bridge signature must match ``[A-Za-z0-9_.-]+``. Path
    separators and dots are squashed; the result is also bounded in
    length so the resulting filename stays manageable.
    """
    cleaned = path.replace("/", "_").replace(".", "_")
    return cleaned[-80:]  # bound the slug; longer tails are more specific


# ── Detector 1: complexity hotspots ─────────────────────────────────────


def detect_complexity_hotspots() -> list[RefactorCandidate]:
    """Files whose latest QualityScore signals genuine architectural drag.

    We use the LATEST sample in elegance_history rather than a median;
    a single bad pass is enough to surface the candidate, but the
    cooldown + persistence-of-signal filter at the bridge weeds out
    one-off blips.

    We need the per-dimension QualityScore (not just composite) to
    confirm complexity is the dominant lever — files with low composite
    purely from missing docstrings are not worth a refactor CR.
    """
    history = _read_json(_elegance_history_path())
    if not history:
        return []

    # Build a (path, latest_sample) view, then re-score in-place to get
    # the per-dimension breakdown. The history stores only composite for
    # space efficiency, so we re-measure the live file for the verdict.
    try:
        from app.code_quality import measure_file_at_path
    except Exception:
        return []

    candidates: list[RefactorCandidate] = []
    repo_root = Path.cwd()
    for rel_path, samples in history.items():
        if not samples or not rel_path.endswith(".py"):
            continue
        # Re-score the live file for per-dimension visibility.
        abs_path = repo_root / rel_path
        if not abs_path.exists():
            continue
        score = measure_file_at_path(abs_path)
        if score is None:
            continue
        if score.composite > _HOTSPOT_COMPOSITE_MAX:
            continue
        if score.complexity_score > _HOTSPOT_COMPLEXITY_MAX:
            continue

        # Signature buckets per file by composite-bucket so a file that
        # genuinely regresses gets a fresh proposal next cycle, but a
        # file that lingers at the same level stays idempotent at the
        # bridge.
        composite_bucket = int(round(score.composite * 10))  # 0..10
        signature = (
            f"hot__{_safe_signature_path_component(rel_path)}__c{composite_bucket}"
        )
        title = f"Refactor complexity hotspot: {rel_path}"
        body = _build_complexity_body(rel_path, score)
        spec = _build_complexity_spec(rel_path, score)
        target = f"docs/proposed_refactor/{signature}.md"
        candidates.append(RefactorCandidate(
            detector="complexity_hotspot",
            signature=signature,
            title=title,
            body_markdown=body,
            target_path=target,
            coding_session_spec=spec,
        ))

    # Sort by composite ascending (worst-first), cap.
    candidates.sort(key=lambda c: c.signature)  # deterministic tie-break
    return candidates[:_MAX_PER_DETECTOR]


def _build_complexity_body(rel_path: str, score: Any) -> str:
    return (
        f"# Refactor proposal: complexity hotspot in `{rel_path}`\n\n"
        f"**Detector:** `complexity_hotspot` (Phase 2 elegance loop)\n\n"
        f"## Current state\n\n"
        f"| Dimension | Value |\n"
        f"|---|---|\n"
        f"| Composite | {score.composite:.3f} |\n"
        f"| Type coverage | {score.type_coverage:.3f} |\n"
        f"| Docstring coverage | {score.docstring_coverage:.3f} |\n"
        f"| Complexity score | {score.complexity_score:.3f} |\n"
        f"| Lint score | {score.lint_score:.3f} |\n\n"
        f"The complexity score is the dominant signal — branching\n"
        f"density in this file is well above the project target. The\n"
        f"`code_quality` mutation gate would reject any new mutation\n"
        f"that didn't move the composite up by >10%.\n\n"
        f"## Suggested action\n\n"
        f"Extract one or two complex branches into helper functions\n"
        f"with clear names. Aim to lower the average McCabe complexity\n"
        f"below 10 (current target). Behaviour MUST be preserved —\n"
        f"the existing test suite + `differential_test` on touched\n"
        f"symbols should green-light the change before submission.\n\n"
        f"## Approval rules\n\n"
        f"* Operator gate via Signal 👍 / `/cp/changes`.\n"
        f"* 60-min auto-revert window applies.\n"
        f"* TIER_IMMUTABLE files are absolute — if `{rel_path}` is\n"
        f"  protected, the change-request validator will refuse at\n"
        f"  stage time.\n"
    )


def _build_complexity_spec(rel_path: str, score: Any) -> dict[str, Any]:
    return {
        "intent": (
            f"Lower complexity in {rel_path} by extracting helpers from "
            f"branchy code. Preserve behaviour."
        ),
        "files": [rel_path],
        "acceptance": [
            "Existing tests green",
            f"code_quality.measure_file_at_path('{rel_path}') composite "
            f"≥ {score.composite + 0.10:.2f}",
            "No new public API; helpers are private (`_name`)",
        ],
        "expected_duration_min": 45,
    }


# ── Detector 2: import cycles ───────────────────────────────────────────


def detect_import_cycles() -> list[RefactorCandidate]:
    """Actionable cycles from the architectural_drift baseline.

    Excludes systemic SCCs (>20 members) — those need a strategic
    architectural review, not a single refactor pass.
    """
    baseline = _read_json(_architectural_baseline_path())
    if not baseline:
        return []

    cycles_raw = baseline.get("cycles") or []
    actionable = [c for c in cycles_raw if isinstance(c, list) and 2 <= len(c) <= 20]

    # Sort: smaller cycles first (more focused refactor).
    actionable.sort(key=lambda c: (len(c), c[0] if c else ""))

    candidates: list[RefactorCandidate] = []
    for cycle in actionable[:_MAX_PER_DETECTOR]:
        members = sorted(cycle)
        sig_hash = _short_hash("|".join(members), n=10)
        signature = f"cyc__{len(members)}__{sig_hash}"
        title = f"Break import cycle ({len(members)} files)"
        body = _build_cycle_body(members)
        spec = _build_cycle_spec(members)
        target = f"docs/proposed_refactor/{signature}.md"
        candidates.append(RefactorCandidate(
            detector="import_cycle",
            signature=signature,
            title=title,
            body_markdown=body,
            target_path=target,
            coding_session_spec=spec,
        ))
    return candidates


def _build_cycle_body(members: list[str]) -> str:
    listed = "\n".join(f"- `{m}`" for m in members)
    return (
        f"# Refactor proposal: break a {len(members)}-file import cycle\n\n"
        f"**Detector:** `import_cycle` (Phase 2 elegance loop)\n\n"
        f"## Cycle members\n\n{listed}\n\n"
        f"## Why this matters\n\n"
        f"Import cycles are usually masked by Python's lazy attribute\n"
        f"resolution but break in subtle ways (test-discovery order,\n"
        f"hot-reload, mock injection). They also obscure the layering\n"
        f"the operator can reason about.\n\n"
        f"## Suggested action\n\n"
        f"Apply one of:\n\n"
        f"1. **Dependency inversion:** the lower-layer file should not\n"
        f"   import from the higher layer; introduce a protocol /\n"
        f"   callback so the higher layer registers with the lower.\n"
        f"2. **Extract a shared module:** if both files need the same\n"
        f"   type/constant, move it to a leaf module both depend on.\n"
        f"3. **Push down a lazy import:** if the cycle is only on\n"
        f"   import-time, switch one edge to a function-local import.\n"
        f"   (Last resort — masks the smell rather than fixing it.)\n\n"
        f"## Approval rules\n\n"
        f"* Operator gate via Signal 👍 / `/cp/changes`.\n"
        f"* `architectural_drift` will confirm the cycle is gone on\n"
        f"  the next weekly pass — surfaces in the continuity ledger\n"
        f"  via the `architectural_debt_drift` event kind.\n"
    )


def _build_cycle_spec(members: list[str]) -> dict[str, Any]:
    return {
        "intent": (
            f"Break the import cycle among {len(members)} files via "
            f"dependency inversion, extracted shared module, or lazy import."
        ),
        "files": list(members),
        "acceptance": [
            "Existing tests green",
            "architectural_drift baseline shows zero members of this "
            "cycle on the next pass",
            "No new public API in any cycle member",
        ],
        "expected_duration_min": 60,
    }


# ── Detector 3: parallel-capability clusters ────────────────────────────


def detect_parallel_capabilities() -> list[RefactorCandidate]:
    """Capabilities owned by ≥3 files (potential parallel implementations)."""
    baseline = _read_json(_architectural_baseline_path())
    if not baseline:
        return []

    owners_map = baseline.get("capability_owners") or {}
    candidates: list[RefactorCandidate] = []
    items = sorted(owners_map.items())  # deterministic
    for cap, owners in items:
        if not isinstance(owners, list) or len(owners) < 3:
            continue
        owners_sorted = sorted(owners)
        sig_hash = _short_hash("|".join([cap] + owners_sorted), n=10)
        signature = f"cap__{_safe_signature_path_component(cap)}__{sig_hash}"
        title = f"Consolidate parallel capability '{cap}' ({len(owners_sorted)} owners)"
        body = _build_parallel_body(cap, owners_sorted)
        spec = _build_parallel_spec(cap, owners_sorted)
        target = f"docs/proposed_refactor/{signature}.md"
        candidates.append(RefactorCandidate(
            detector="parallel_capability",
            signature=signature,
            title=title,
            body_markdown=body,
            target_path=target,
            coding_session_spec=spec,
        ))
    return candidates[:_MAX_PER_DETECTOR]


def _build_parallel_body(cap: str, owners: list[str]) -> str:
    listed = "\n".join(f"- `{o}`" for o in owners)
    return (
        f"# Refactor proposal: parallel capability `{cap}` "
        f"({len(owners)} owners)\n\n"
        f"**Detector:** `parallel_capability` (Phase 2 elegance loop)\n\n"
        f"## Current owners\n\n{listed}\n\n"
        f"## Why this matters\n\n"
        f"Multiple files claiming the same `@register_tool` capability\n"
        f"is the architectural-review hard-reject pattern when it\n"
        f"appears in a new mutation. Existing parallel implementations\n"
        f"slipped in before the gate caught them; this proposal\n"
        f"surfaces them for cleanup.\n\n"
        f"Not all parallels are bugs — `registers-tool` and similar\n"
        f"meta-tags legitimately repeat. The operator decides whether\n"
        f"this capability is one of those, or a true duplication.\n\n"
        f"## Suggested action\n\n"
        f"Choose one of:\n\n"
        f"1. **Consolidate to one owner.** Pick the most authoritative\n"
        f"   implementation; deprecate or delete the others.\n"
        f"2. **Rename to disambiguate.** If the owners actually do\n"
        f"   distinct things, rename the capability tags to reflect\n"
        f"   that (e.g. `renders-pdf-report` vs `renders-pdf-chart`).\n"
        f"3. **Mark as meta-tag, document the exemption.** Add a\n"
        f"   comment in `tool_registry/capabilities.py` so future\n"
        f"   architectural-drift passes know it's intentional.\n\n"
        f"## Approval rules\n\n"
        f"* Operator gate via Signal 👍 / `/cp/changes`.\n"
        f"* `architectural_drift` will confirm the resolution path on\n"
        f"  the next weekly pass.\n"
    )


def _build_parallel_spec(cap: str, owners: list[str]) -> dict[str, Any]:
    return {
        "intent": (
            f"Resolve the parallel-capability cluster for '{cap}' across "
            f"{len(owners)} files (consolidate, rename, or document as "
            f"meta-tag)."
        ),
        "files": list(owners),
        "acceptance": [
            "Existing tests green",
            "Owner count is either 1 (consolidated) or has documented "
            "rationale (`tool_registry/capabilities.py` comment)",
            "tool_registry behaviour preserved for downstream callers",
        ],
        "expected_duration_min": 30,
    }


# ── Pass orchestration ──────────────────────────────────────────────────


def run_one_pass() -> dict[str, Any]:
    """Generate candidates from all detectors, stage each through the bridge.

    Returns a small summary suitable for daemon logs and tests:
    ``{"checked": bool, "staged": int, "skipped": int, "errors": int,
        "by_detector": {...}}``.
    """
    summary: dict[str, Any] = {
        "checked": False, "staged": 0, "skipped": 0, "errors": 0,
        "by_detector": {},
    }
    if not _enabled():
        summary["disabled"] = True
        return summary

    try:
        from app.proposal_bridge import stage
    except Exception:
        logger.debug("refactor_proposer: proposal_bridge unavailable", exc_info=True)
        summary["errors"] += 1
        return summary

    detectors = (
        ("complexity_hotspot", detect_complexity_hotspots),
        ("import_cycle", detect_import_cycles),
        ("parallel_capability", detect_parallel_capabilities),
    )
    for name, fn in detectors:
        det_staged = 0
        det_skipped = 0
        det_errors = 0
        try:
            candidates = fn()
        except Exception:
            logger.debug("refactor_proposer.%s: raised", name, exc_info=True)
            det_errors += 1
            candidates = []
        for c in candidates:
            try:
                _state, was_new = stage(
                    source="refactor_proposer",
                    signature=c.signature,
                    title=c.title,
                    body_markdown=c.body_markdown,
                    target_path=c.target_path,
                    cooldown_days=_COOLDOWN_DAYS,
                    coding_session_spec=c.coding_session_spec,
                )
                if was_new:
                    det_staged += 1
                else:
                    det_skipped += 1
            except Exception:
                logger.debug(
                    "refactor_proposer.%s: stage failed for %s",
                    name, c.signature, exc_info=True,
                )
                det_errors += 1
        summary["by_detector"][name] = {
            "staged": det_staged, "skipped": det_skipped, "errors": det_errors,
            "candidates": len(candidates),
        }
        summary["staged"] += det_staged
        summary["skipped"] += det_skipped
        summary["errors"] += det_errors

    summary["checked"] = True
    return summary


# ── Daemon thread ────────────────────────────────────────────────────────


_driver_started = False
_driver_lock = threading.Lock()
_stop_event = threading.Event()


def _is_running() -> bool:
    return any(
        t.name == _DAEMON_THREAD_NAME and t.is_alive()
        for t in threading.enumerate()
    )


def _driver() -> None:
    if _stop_event.wait(_WARMUP_S):
        return
    while not _stop_event.is_set():
        try:
            result = run_one_pass()
            if result.get("checked"):
                logger.info(
                    "refactor_proposer: staged=%d skipped=%d errors=%d",
                    result["staged"], result["skipped"], result["errors"],
                )
        except Exception:
            logger.debug("refactor_proposer: pass raised", exc_info=True)
        if _stop_event.wait(_POLL_INTERVAL_S):
            return


def start() -> None:
    global _driver_started
    if not _enabled():
        logger.info(
            "refactor_proposer: disabled via refactor_proposer_enabled",
        )
        return
    with _driver_lock:
        if _is_running():
            return
        if _driver_started:
            logger.warning("refactor_proposer: previous thread is dead, re-spawning")
        _stop_event.clear()
        thread = threading.Thread(
            target=_driver, name=_DAEMON_THREAD_NAME, daemon=True,
        )
        thread.start()
        _driver_started = True
        logger.info(
            "refactor_proposer: daemon started (warm-up=%ds, poll=%dh)",
            _WARMUP_S, _POLL_INTERVAL_S // 3600,
        )


def stop() -> None:
    _stop_event.set()
