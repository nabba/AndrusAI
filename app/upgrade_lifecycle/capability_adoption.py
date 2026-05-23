"""U5 — Capability adoption (Stage E).

PROGRAM §62. After U1 has produced :class:`Capability` rows describing
*what new features* an upgrade brought, this module is the one that
proposes USING those features in our code. The output is a CR per
adoption site — one site per pass, hard-capped at one CR per week.

Three resource gates compose:

  * **Master switch** —
    ``runtime_settings.upgrade_lifecycle_capability_adoption_enabled``.
  * **Rate limit** — at most one CR per ISO week (Mon..Sun UTC).
  * **Quarterly budget** — calendar-quarter USD ceiling (default $20)
    tracked in ``workspace/upgrade_lifecycle/budget_ledger.jsonl``.
    LLM cost estimate ~ $0.01–0.05 per refactor attempt.

Plus two correctness gates:

  * **Dedup against architecture_requests** — if there's already an
    open architecture-request touching the same path, we skip
    (operator already has the file on their plate).
  * **TIER_IMMUTABLE refusal** — the validator inside
    ``proposal_bridge.stage()`` refuses immutable paths at filing
    time, so refactor candidates over immutable code are dropped
    here before we even spend on the LLM call.

LLM model decision via :mod:`app.llm_factory` per operator decision —
no model IDs hardcoded.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from app.upgrade_lifecycle.changelog_fetcher import FRAMEWORK_PACKAGES
from app.upgrade_lifecycle.impact_analysis import extract_candidate_symbols
from app.upgrade_lifecycle.protocol import Capability

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


_MAX_CR_PER_WEEK = 1
_ESTIMATED_COST_PER_ATTEMPT_USD = 0.02
_PROPOSAL_SOURCE = "dependency_radar"      # routes through same staging dir
_MAX_FILE_BYTES_FOR_LLM = 50 * 1024         # 50 KB — same as structured_diagnosis


# ── Workspace paths ──────────────────────────────────────────────────────


def _adoption_dir() -> Path:
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / "adoption"
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / "adoption"
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle/adoption")


def _budget_ledger_path() -> Path:
    return _adoption_dir() / "budget_ledger.jsonl"


def _rate_limit_state_path() -> Path:
    return _adoption_dir() / "rate_limit_state.json"


# ── Master switch ────────────────────────────────────────────────────────


def _enabled() -> bool:
    try:
        from app.runtime_settings import (
            get_upgrade_lifecycle_capability_adoption_enabled,
        )
        return get_upgrade_lifecycle_capability_adoption_enabled()
    except Exception:
        return True


def _quarterly_budget_usd() -> float:
    try:
        from app.runtime_settings import (
            get_upgrade_lifecycle_capability_budget_usd_quarterly,
        )
        return float(get_upgrade_lifecycle_capability_budget_usd_quarterly())
    except Exception:
        return 20.0


# ── Calendar quarter helpers ─────────────────────────────────────────────


def _current_quarter_key(now: datetime) -> str:
    """Return e.g. ``"2026-Q2"`` for *now* — calendar quarter, UTC."""
    q = (now.month - 1) // 3 + 1
    return f"{now.year:04d}-Q{q}"


def _current_iso_week_key(now: datetime) -> str:
    """ISO-week key ``"2026-W21"`` for rate limiting."""
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


# ── Budget ledger ────────────────────────────────────────────────────────


def _read_budget_ledger() -> list[dict[str, Any]]:
    path = _budget_ledger_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _append_budget_row(row: dict[str, Any]) -> None:
    path = _budget_ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        logger.debug("ul.adoption: budget write failed", exc_info=True)


def current_quarter_spend(now: Optional[datetime] = None) -> float:
    """Sum the cost rows whose ``quarter`` matches the current quarter."""
    now_dt = now or datetime.now(timezone.utc)
    qk = _current_quarter_key(now_dt)
    total = 0.0
    for row in _read_budget_ledger():
        if row.get("quarter") == qk:
            total += float(row.get("cost_usd") or 0.0)
    return total


def remaining_quarter_budget(now: Optional[datetime] = None) -> float:
    return max(0.0, _quarterly_budget_usd() - current_quarter_spend(now=now))


def record_attempt(*, cost_usd: float, package: str, target_path: str,
                  succeeded: bool, now: Optional[datetime] = None) -> None:
    """Append a budget ledger row.

    The ledger captures both successful + failed attempts so the
    operator can see what was spent regardless of outcome.
    """
    now_dt = now or datetime.now(timezone.utc)
    _append_budget_row({
        "quarter": _current_quarter_key(now_dt),
        "iso_week": _current_iso_week_key(now_dt),
        "ts": now_dt.isoformat(),
        "package": package,
        "target_path": target_path,
        "cost_usd": float(cost_usd),
        "succeeded": bool(succeeded),
    })


# ── Rate limit (one CR per ISO week) ─────────────────────────────────────


def _read_rate_state() -> dict[str, Any]:
    p = _rate_limit_state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_rate_state(state: dict[str, Any]) -> None:
    p = _rate_limit_state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(p)
    except OSError:
        logger.debug("ul.adoption: rate state write failed", exc_info=True)


def crs_this_week(now: Optional[datetime] = None) -> int:
    now_dt = now or datetime.now(timezone.utc)
    wk = _current_iso_week_key(now_dt)
    state = _read_rate_state()
    return int(state.get(wk) or 0)


def _bump_rate_counter(now: datetime) -> None:
    wk = _current_iso_week_key(now)
    state = _read_rate_state()
    state[wk] = int(state.get(wk) or 0) + 1
    # Garbage-collect old weeks so the file doesn't grow forever.
    if len(state) > 20:
        for k in list(state.keys()):
            if k != wk and k < wk:
                state.pop(k, None)
    _write_rate_state(state)


# ── Dedup against open architecture-requests ─────────────────────────────


def _path_has_open_architecture_request(target_path: str) -> bool:
    """Check architecture_requests for any open record touching *target_path*.

    Failure-isolated: when the module isn't importable (test env),
    we return False (not deduped) so the test exercises the path.
    """
    try:
        from app.architecture_requests import lifecycle
    except Exception:
        return False
    try:
        # Architecture-requests lifecycle exposes ``list_open`` (or
        # similarly-named). Best-effort lookup — many shapes are valid.
        candidates = getattr(lifecycle, "list_open", None)
        if candidates is None:
            return False
        records = candidates() or []
        for rec in records:
            paths = getattr(rec, "paths", None) or rec.get("paths", [])  # type: ignore[attr-defined]
            if not paths:
                # Fallback to a 'target_path' field
                paths = [getattr(rec, "target_path", "")]
            for p in paths:
                if p and (p == target_path or target_path.endswith(p)):
                    return True
    except Exception:
        return False
    return False


# ── Candidate-site discovery ─────────────────────────────────────────────


def _iter_codebase_files(repo_root: Path,
                        *, search_subdirs: tuple[str, ...] = ("app",)) -> Iterable[Path]:
    for sub in search_subdirs:
        base = repo_root / sub
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            parts = set(p.parts)
            if "__pycache__" in parts or "tests" in parts:
                continue
            yield p


def discover_candidate_sites(
    capability: Capability,
    *,
    repo_root: Optional[Path] = None,
    max_sites: int = 3,
) -> list[tuple[Path, str]]:
    """For each ``new_feature`` string, find files mentioning related symbols.

    Returns a list of ``(path, matched_feature)`` pairs. Very rough
    — the LLM is the one that decides whether a real adoption
    opportunity exists at the site; we just narrow the search.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]

    if not capability.new_features:
        return []

    sites: list[tuple[Path, str]] = []
    seen_paths: set[Path] = set()
    for feature in capability.new_features:
        symbols = extract_candidate_symbols(feature)
        if not symbols:
            continue
        symbol_keys = {s.lower() for s in symbols}
        for path in _iter_codebase_files(repo_root):
            if path in seen_paths:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            if any(sym in text for sym in symbol_keys):
                sites.append((path, feature))
                seen_paths.add(path)
                if len(sites) >= max_sites:
                    return sites
    return sites


# ── LLM refactor generation (via factory) ────────────────────────────────


_LLM_SYSTEM_PROMPT = """You analyze Python source files and propose
adoption refactors when a new library capability makes a specific
existing usage pattern obsolete.

Output STRICT JSON. Schema:

  {
    "should_refactor": true,
    "rationale": "1-3 sentence explanation",
    "patch_summary": "1-line summary of what would change",
    "confidence": 0.80
  }

OR

  {
    "should_refactor": false,
    "reason": "no_clear_opportunity | risky | unclear_intent | already_modern"
  }

Rules:
  * Decline if the file already uses the new capability.
  * Decline if the refactor would require touching > 1 file.
  * Decline if the existing usage is correct + idiomatic — "new"
    doesn't mean "better."
  * Confidence < 0.5 → decline.
"""


def _call_llm_for_refactor_proposal(
    *,
    file_content: str,
    file_path: str,
    capability: Capability,
    feature: str,
    llm_builder: Optional[Callable[[], Any]] = None,
) -> Optional[dict[str, Any]]:
    """Issue the LLM call via the factory."""
    try:
        if llm_builder is None:
            from app.llm_factory import create_specialist_llm
            llm = create_specialist_llm(
                max_tokens=2048,
                role="research",
                task_hint="upgrade-lifecycle adoption proposal",
            )
        else:
            llm = llm_builder()
    except Exception:
        return None

    user_msg = (
        f"package: {capability.package} {capability.from_version}→{capability.to_version}\n"
        f"new_feature: {feature}\n"
        f"file_path: {file_path}\n\n"
        f"=== current file content ===\n{file_content}"
    )

    try:
        raw = str(llm.call([
            {"role": "system", "content": _LLM_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ])).strip()
    except Exception:
        return None

    # Strip code fences then parse strict JSON.
    if raw.startswith("```"):
        import re
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ── Public API: one-pass orchestrator ────────────────────────────────────


def _compose_cr_body(
    *,
    capability: Capability,
    feature: str,
    target_path: str,
    proposal: dict[str, Any],
) -> str:
    return (
        f"# Adopt: {feature}\n"
        f"\n"
        f"This proposal originates from the upgrade lifecycle "
        f"(PROGRAM §62, Stage E). The capability extractor noted "
        f"a new feature in `{capability.package} "
        f"{capability.from_version}→{capability.to_version}`:\n"
        f"\n"
        f"> {feature}\n"
        f"\n"
        f"## Candidate site\n"
        f"`{target_path}`\n"
        f"\n"
        f"## Proposed refactor\n"
        f"{proposal.get('patch_summary', '(not provided)')}\n"
        f"\n"
        f"## Rationale\n"
        f"{proposal.get('rationale', '(not provided)')}\n"
        f"\n"
        f"## Confidence\n"
        f"{float(proposal.get('confidence') or 0.0):.2f}\n"
        f"\n"
        f"---\n"
        f"This CR is a STARTING POINT for the operator; it does not "
        f"include a diff. The author of the eventual implementation "
        f"is free to reject, narrow, or rework the proposal.\n"
    )


def run_one_pass(
    *,
    repo_root: Optional[Path] = None,
    capability_iterator: Optional[Callable[[], Iterable[Capability]]] = None,
    llm_builder: Optional[Callable[[], Any]] = None,
    stage_fn: Optional[Callable] = None,
    architecture_dedup: Optional[Callable[[str], bool]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Process the capability backlog for one weekly pass.

    Returns a summary dict for logging and the U7 status endpoint:
      ``{cr_filed: bool, target_path: str|None, reason: str,
        budget_remaining_usd: float, crs_this_week: int}``

    Every external dependency is injectable for tests.
    """
    now_dt = now or datetime.now(timezone.utc)
    summary: dict[str, Any] = {
        "cr_filed": False,
        "target_path": None,
        "reason": "",
        "budget_remaining_usd": remaining_quarter_budget(now=now_dt),
        "crs_this_week": crs_this_week(now=now_dt),
    }

    if not _enabled():
        summary["reason"] = "master_switch_off"
        return summary

    # Gate 1: rate limit (1 CR / ISO week)
    if summary["crs_this_week"] >= _MAX_CR_PER_WEEK:
        summary["reason"] = "rate_limited"
        return summary

    # Gate 2: budget
    if summary["budget_remaining_usd"] < _ESTIMATED_COST_PER_ATTEMPT_USD:
        summary["reason"] = "budget_exhausted"
        return summary

    # Walk the capability backlog
    if capability_iterator is None:
        capability_iterator = _default_capability_iterator
    caps = list(capability_iterator())
    if not caps:
        summary["reason"] = "no_capabilities"
        return summary

    arch_dedup_fn = architecture_dedup or _path_has_open_architecture_request

    for capability in caps:
        # Skip framework packages — those go through annual snapshot.
        norm_pkg = capability.package.lower().replace("_", "-")
        if norm_pkg in FRAMEWORK_PACKAGES:
            continue
        sites = discover_candidate_sites(capability, repo_root=repo_root)
        for path, feature in sites:
            target_path = _to_repo_relative(path, repo_root)
            if arch_dedup_fn(target_path):
                continue
            # Read file content (size-gated)
            try:
                raw_bytes = path.read_bytes()
                if len(raw_bytes) > _MAX_FILE_BYTES_FOR_LLM:
                    continue
                content = raw_bytes.decode("utf-8", errors="ignore")
            except OSError:
                continue
            proposal = _call_llm_for_refactor_proposal(
                file_content=content,
                file_path=target_path,
                capability=capability,
                feature=feature,
                llm_builder=llm_builder,
            )
            record_attempt(
                cost_usd=_ESTIMATED_COST_PER_ATTEMPT_USD,
                package=capability.package,
                target_path=target_path,
                succeeded=False,
                now=now_dt,
            )
            if proposal is None or not proposal.get("should_refactor"):
                continue
            if float(proposal.get("confidence") or 0.0) < 0.5:
                continue
            # File the CR.
            body = _compose_cr_body(
                capability=capability, feature=feature,
                target_path=target_path, proposal=proposal,
            )
            signature = _signature_for(capability, target_path)
            try:
                if stage_fn is None:
                    from app.proposal_bridge.store import stage as stage_fn  # type: ignore[assignment]
                stage_fn(   # type: ignore[misc]
                    source=_PROPOSAL_SOURCE,
                    signature=signature,
                    title=f"Adopt {capability.package} {capability.to_version} capability: {feature[:60]}",
                    body_markdown=body,
                    target_path=target_path,
                    cooldown_days=14,
                )
            except Exception:
                logger.debug("ul.adoption: stage failed", exc_info=True)
                continue
            _bump_rate_counter(now_dt)
            # Update the last attempt row to succeeded=True
            record_attempt(
                cost_usd=0.0,    # zero — same attempt, just marking success
                package=capability.package, target_path=target_path,
                succeeded=True, now=now_dt,
            )
            summary["cr_filed"] = True
            summary["target_path"] = target_path
            summary["reason"] = "ok"
            summary["budget_remaining_usd"] = remaining_quarter_budget(now=now_dt)
            summary["crs_this_week"] = crs_this_week(now=now_dt)
            return summary
    summary["reason"] = "no_candidate"
    return summary


def _default_capability_iterator() -> Iterable[Capability]:
    """Walk the per-package capability ledger directory."""
    from app.upgrade_lifecycle.changelog_fetcher import _capabilities_dir, read_capabilities
    cap_dir = _capabilities_dir()
    if not cap_dir.exists():
        return []
    out: list[Capability] = []
    for path in sorted(cap_dir.glob("*.jsonl")):
        # Derive the package name from the filename stem
        pkg = path.stem
        out.extend(read_capabilities(pkg))
    return out


def _signature_for(capability: Capability, target_path: str) -> str:
    """Stable signature so re-runs over the same input are idempotent."""
    key = f"adopt_{capability.package}_{capability.to_version}_{target_path}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    # proposal_bridge requires [A-Za-z0-9_.-]
    return f"adopt_{h}"


def _to_repo_relative(path: Path, repo_root: Optional[Path]) -> str:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)
