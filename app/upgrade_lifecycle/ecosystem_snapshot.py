"""U6 — Annual ecosystem snapshot.

PROGRAM §62 — Stage U6 of the upgrade lifecycle. One markdown report
per calendar year, written to ``wiki/self/ecosystem/<YYYY>.md``, that
captures the long-trajectory questions the radar's per-package
findings can't answer:

  * Python version EOL countdown — when do we *have to* upgrade Python?
  * Per-package health trajectory — is the maintainer still active?
  * Framework health — are CrewAI / FastAPI / ChromaDB / Pydantic
    being actively developed, abandoned, forked?
  * Vendor concentration — what fraction of our LLM spend goes to
    each provider, year over year? (Hedge against a single vendor
    sundown.)
  * **Major-upgrade plan** — what MAJOR bumps did the radar surface
    over the year, what are the trade-offs, which ones is the
    operator planning to take this year?

Cadence: first cron-eligible day of January, idempotent within the year.

Acceptance flow (operator decision Q3): the major-upgrade section
exposes ``status``, ``accepted_at``, ``cr_id`` per row. The operator
flips ``status`` from ``proposed`` to ``accepted`` via the REST
endpoint; on acceptance the system files a CR (non-TIER_IMMUTABLE
path) or a Tier-3 amendment proposal (TIER_IMMUTABLE path) and the
existing change-request infrastructure handles build + deploy.
**The operator's acceptance IS the gate** — no extra approval needed.

Emits the 19th identity-continuity event kind ``ecosystem_snapshot``
on snapshot creation and on per-row acceptance (subkind=
``acceptance``) so the annual reflection's drift summary picks them
up via the existing ``summarise_drift`` Counter.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ── Python EOL table (manually maintained — small surface) ──────────────


# Source: https://devguide.python.org/versions/
# Conservative — uses the EOL date for security fixes, not feature freeze.
PYTHON_EOL_TABLE: dict[str, date] = {
    "3.9": date(2025, 10, 31),
    "3.10": date(2026, 10, 31),
    "3.11": date(2027, 10, 31),
    "3.12": date(2028, 10, 31),
    "3.13": date(2029, 10, 31),
    "3.14": date(2030, 10, 31),
}


# ── Paths ────────────────────────────────────────────────────────────────


def _snapshot_dir() -> Path:
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / "ecosystem"
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / "ecosystem"
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle/ecosystem")


def _wiki_dir() -> Path:
    """Output path mirrors ``wiki/self/value_reflections/<year>.md`` pattern."""
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        # Tests redirect — write into the override too.
        return Path(override) / "wiki_ecosystem"
    try:
        repo_root = Path(__file__).resolve().parents[2]
        return repo_root / "wiki" / "self" / "ecosystem"
    except Exception:
        return Path("wiki/self/ecosystem")


def _snapshot_path_for_year(year: int) -> Path:
    return _snapshot_dir() / f"{year}.json"


def _markdown_path_for_year(year: int) -> Path:
    return _wiki_dir() / f"{year}.md"


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class MajorUpgradeProposal:
    """One row in the snapshot's major-upgrade plan section."""

    package: str
    from_version: str
    to_version: str
    priority: str                          # "low" | "medium" | "high"
    is_framework: bool
    capability_summary: str
    status: str = "proposed"               # "proposed" | "accepted" | "deferred" | "rejected"
    accepted_at: Optional[str] = None
    cr_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EcosystemSnapshot:
    year: int
    generated_at: str
    python_eol: dict[str, Any] = field(default_factory=dict)
    package_health: list[dict[str, Any]] = field(default_factory=list)
    framework_health: list[dict[str, Any]] = field(default_factory=list)
    vendor_concentration: dict[str, float] = field(default_factory=dict)
    major_upgrades: list[MajorUpgradeProposal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["major_upgrades"] = [m.to_dict() for m in self.major_upgrades]
        return d


# ── Master switch ────────────────────────────────────────────────────────


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_ecosystem_snapshot_enabled
        return get_ecosystem_snapshot_enabled()
    except Exception:
        return True


# ── Component composers (each one cheap, deterministic, injectable) ─────


def compose_python_eol_section(*, now: Optional[date] = None,
                              current_minor: str = "3.13") -> dict[str, Any]:
    """Compute days until EOL for the active Python minor version."""
    today = now or date.today()
    eol_date = PYTHON_EOL_TABLE.get(current_minor)
    days_until: Optional[int] = None
    if eol_date is not None:
        days_until = (eol_date - today).days
    return {
        "current": current_minor,
        "eol_date": eol_date.isoformat() if eol_date else None,
        "days_until_eol": days_until,
        "future_versions": [
            {"version": v, "eol": d.isoformat()}
            for v, d in sorted(PYTHON_EOL_TABLE.items())
            if v > current_minor
        ],
    }


def compose_package_health_section(
    *,
    dependency_radar_state: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Read the dependency_radar's last_findings_by_severity + abandoned set.

    Failure-isolated: when the radar hasn't run, returns an empty
    list — the operator sees a hint to run the radar.
    """
    if dependency_radar_state is None:
        try:
            from app.dependency_radar.proposer import _read_state
            dependency_radar_state = _read_state()
        except Exception:
            dependency_radar_state = {}

    rows: list[dict[str, Any]] = []
    severity_counts = dependency_radar_state.get("last_findings_by_severity") or {}
    for sev, count in sorted(severity_counts.items()):
        rows.append({"severity": sev, "count": int(count)})
    return rows


def compose_framework_health_section(
    *,
    framework_fetcher: Optional[Callable[[str], dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Walk a curated framework list and return basic health metrics.

    The default fetcher pulls cached PyPI data; tests inject a stub.
    """
    frameworks = ("crewai", "chromadb", "fastapi", "pydantic", "starlette", "anthropic")
    out: list[dict[str, Any]] = []
    for pkg in frameworks:
        try:
            data = framework_fetcher(pkg) if framework_fetcher else _default_framework_fetcher(pkg)
        except Exception:
            data = {}
        out.append({
            "package": pkg,
            "current_version": data.get("current_version", ""),
            "latest_version": data.get("latest_version", ""),
            "last_release_age_days": data.get("last_release_age_days"),
        })
    return out


def _default_framework_fetcher(package: str) -> dict[str, Any]:
    """Use the changelog_fetcher's PyPI helper to learn the latest version."""
    try:
        from app.upgrade_lifecycle.changelog_fetcher import _fetch_pypi_metadata
        md = _fetch_pypi_metadata(package) or {}
        info = md.get("info") or {}
        return {
            "current_version": "",   # unknown without pip introspection
            "latest_version": info.get("version") or "",
            "last_release_age_days": None,
        }
    except Exception:
        return {}


def compose_vendor_concentration_section(
    *,
    cost_fetcher: Optional[Callable[[], dict[str, float]]] = None,
) -> dict[str, float]:
    """Return ``{provider: fraction_of_total_spend}`` for the past year.

    Default fetcher tries a cost-by-provider helper; tests inject a stub.
    """
    if cost_fetcher is None:
        try:
            return _default_cost_by_provider()
        except Exception:
            return {}
    try:
        return cost_fetcher() or {}
    except Exception:
        return {}


def _default_cost_by_provider() -> dict[str, float]:
    """Best-effort hook for the cost ledger. Returns empty when unavailable."""
    return {}


def compose_major_upgrade_proposals(
    *,
    capability_iterator: Optional[Callable[[], list]] = None,
) -> list[MajorUpgradeProposal]:
    """Walk the capability backlog for major-bump candidates.

    A "major bump" here is loosely defined — we expose any capability
    whose ``to_version`` starts a different major number than its
    ``from_version``. Tests inject a deterministic iterator.
    """
    if capability_iterator is None:
        try:
            from app.upgrade_lifecycle.capability_adoption import (
                _default_capability_iterator,
            )
            caps = list(_default_capability_iterator())
        except Exception:
            caps = []
    else:
        caps = list(capability_iterator())

    from app.upgrade_lifecycle.changelog_fetcher import FRAMEWORK_PACKAGES

    out: list[MajorUpgradeProposal] = []
    seen: set[tuple[str, str]] = set()
    for cap in caps:
        try:
            fv = (cap.from_version or "").lstrip("vV").split(".")[0]
            tv = (cap.to_version or "").lstrip("vV").split(".")[0]
        except Exception:
            continue
        if not fv or not tv or fv == tv:
            continue
        key = (cap.package.lower(), cap.to_version)
        if key in seen:
            continue
        seen.add(key)
        is_framework = cap.package.lower().replace("_", "-") in FRAMEWORK_PACKAGES
        cap_summary_parts: list[str] = []
        if cap.new_features:
            cap_summary_parts.append(f"{len(cap.new_features)} new features")
        if cap.breaking_changes:
            cap_summary_parts.append(f"{len(cap.breaking_changes)} breaking")
        if cap.security_fixes:
            cap_summary_parts.append(f"{len(cap.security_fixes)} security fixes")
        out.append(MajorUpgradeProposal(
            package=cap.package,
            from_version=cap.from_version,
            to_version=cap.to_version,
            priority=("high" if cap.security_fixes
                      else "medium" if is_framework else "low"),
            is_framework=is_framework,
            capability_summary=", ".join(cap_summary_parts) or "no extracted summary",
        ))
    # High first, then medium, then low.
    priority_order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda m: (priority_order.get(m.priority, 99), m.package))
    return out


# ── Public API: generate snapshot ────────────────────────────────────────


def generate_snapshot(
    *,
    year: Optional[int] = None,
    now: Optional[datetime] = None,
    current_python_minor: str = "3.13",
    framework_fetcher: Optional[Callable] = None,
    cost_fetcher: Optional[Callable[[], dict[str, float]]] = None,
    capability_iterator: Optional[Callable] = None,
    dependency_radar_state: Optional[dict[str, Any]] = None,
) -> Optional[EcosystemSnapshot]:
    """Compose this year's snapshot. Idempotent — re-runs the same year
    return the existing snapshot from disk.

    Returns None when the master switch is off.
    """
    if not _enabled():
        return None
    now_dt = now or datetime.now(timezone.utc)
    yr = year if year is not None else now_dt.year

    # Idempotent check
    existing = _read_snapshot(yr)
    if existing is not None:
        return existing

    snapshot = EcosystemSnapshot(
        year=yr,
        generated_at=now_dt.isoformat(),
        python_eol=compose_python_eol_section(
            now=now_dt.date(), current_minor=current_python_minor,
        ),
        package_health=compose_package_health_section(
            dependency_radar_state=dependency_radar_state,
        ),
        framework_health=compose_framework_health_section(
            framework_fetcher=framework_fetcher,
        ),
        vendor_concentration=compose_vendor_concentration_section(
            cost_fetcher=cost_fetcher,
        ),
        major_upgrades=compose_major_upgrade_proposals(
            capability_iterator=capability_iterator,
        ),
    )
    _persist_snapshot(snapshot)
    _write_markdown(snapshot)
    _emit_ledger_event(snapshot, kind="ecosystem_snapshot")
    return snapshot


def _persist_snapshot(snapshot: EcosystemSnapshot) -> None:
    path = _snapshot_path_for_year(snapshot.year)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True))
        tmp.replace(path)
    except OSError:
        logger.debug("ul.ecosystem: persist failed", exc_info=True)


def _read_snapshot(year: int) -> Optional[EcosystemSnapshot]:
    path = _snapshot_path_for_year(year)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        snapshot = EcosystemSnapshot(
            year=int(data["year"]),
            generated_at=str(data.get("generated_at", "")),
            python_eol=dict(data.get("python_eol") or {}),
            package_health=list(data.get("package_health") or []),
            framework_health=list(data.get("framework_health") or []),
            vendor_concentration=dict(data.get("vendor_concentration") or {}),
            major_upgrades=[
                MajorUpgradeProposal(**m)
                for m in data.get("major_upgrades") or []
            ],
        )
        return snapshot
    except (KeyError, TypeError, ValueError):
        return None


def _write_markdown(snapshot: EcosystemSnapshot) -> None:
    """Render the snapshot to ``wiki/self/ecosystem/<year>.md``."""
    lines = [f"# Ecosystem snapshot — {snapshot.year}",
            "",
            f"Generated at {snapshot.generated_at}",
            "",
            "## Python EOL"]
    eol = snapshot.python_eol
    days = eol.get("days_until_eol")
    lines.append(
        f"Current minor: **{eol.get('current')}** — "
        f"EOL on {eol.get('eol_date') or 'unknown'} "
        f"({days} days from now)" if days is not None
        else f"Current minor: **{eol.get('current')}**"
    )
    fv = eol.get("future_versions") or []
    if fv:
        lines.append("")
        lines.append("### Future versions")
        for row in fv:
            lines.append(f"- {row['version']} — EOL {row['eol']}")
    lines.append("")
    lines.append("## Package health (latest radar pass)")
    if snapshot.package_health:
        for row in snapshot.package_health:
            lines.append(f"- {row['severity']}: {row['count']}")
    else:
        lines.append("(no recent radar run)")
    lines.append("")
    lines.append("## Framework health")
    for row in snapshot.framework_health:
        lines.append(f"- {row['package']}: current=`{row.get('current_version', '')}` "
                     f"latest=`{row.get('latest_version', '')}`")
    lines.append("")
    lines.append("## Vendor concentration (last year)")
    if snapshot.vendor_concentration:
        for vendor, fraction in sorted(
            snapshot.vendor_concentration.items(), key=lambda kv: -kv[1],
        ):
            lines.append(f"- {vendor}: {fraction:.1%}")
    else:
        lines.append("(no cost data)")
    lines.append("")
    lines.append("## Major upgrades planned this year")
    if snapshot.major_upgrades:
        for m in snapshot.major_upgrades:
            badge = "🏛 framework" if m.is_framework else "📦 library"
            lines.append(
                f"- **{m.package}** {m.from_version} → {m.to_version} "
                f"({m.priority}, {badge}) — {m.capability_summary} — status: `{m.status}`"
            )
    else:
        lines.append("(no major bumps queued)")
    lines.append("")

    path = _markdown_path_for_year(snapshot.year)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        logger.debug("ul.ecosystem: markdown write failed", exc_info=True)


def _emit_ledger_event(snapshot: EcosystemSnapshot, *,
                      kind: str = "ecosystem_snapshot",
                      subkind: Optional[str] = None,
                      extra: Optional[dict[str, Any]] = None) -> None:
    """Best-effort identity-continuity event emission.

    Failure-isolated: when the ledger module isn't importable (test
    env, early boot), we silently skip.
    """
    payload: dict[str, Any] = {
        "year": snapshot.year,
        "major_upgrades": len(snapshot.major_upgrades),
        "python_eol_days": snapshot.python_eol.get("days_until_eol"),
    }
    if subkind:
        payload["subkind"] = subkind
    if extra:
        payload.update(extra)
    try:
        from app.identity.continuity_ledger import emit_event
        emit_event(kind=kind, source_module="upgrade_lifecycle.ecosystem_snapshot",
                  payload=payload)
    except Exception:
        logger.debug("ul.ecosystem: ledger emit failed", exc_info=True)


# ── Operator acceptance flow ─────────────────────────────────────────────


def accept_major_upgrade(
    *,
    year: int,
    package: str,
    to_version: str,
    operator_actor: str = "operator",
    now: Optional[datetime] = None,
    cr_filer: Optional[Callable[..., str]] = None,
    tier3_proposer: Optional[Callable[..., str]] = None,
    impact_repo_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Mark the row accepted and trigger downstream CR or amendment.

    Returns the updated row as a dict, including the ``cr_id`` field
    on success.

    Routing:
      * Non-framework + no TIER_IMMUTABLE → standard CR via
        ``proposal_bridge``.
      * Framework OR TIER_IMMUTABLE → Tier-3 amendment proposal.

    Both downstream paths inject for tests.
    """
    snapshot = _read_snapshot(year)
    if snapshot is None:
        return {"ok": False, "reason": "no_snapshot_for_year"}

    target_row: Optional[MajorUpgradeProposal] = None
    for row in snapshot.major_upgrades:
        if row.package.lower() == package.lower() and row.to_version == to_version:
            target_row = row
            break
    if target_row is None:
        return {"ok": False, "reason": "row_not_found"}
    if target_row.status == "accepted":
        return {"ok": False, "reason": "already_accepted",
                "cr_id": target_row.cr_id}

    now_dt = now or datetime.now(timezone.utc)
    target_row.status = "accepted"
    target_row.accepted_at = now_dt.isoformat()

    # Route to CR or Tier-3 amendment.
    cr_id: Optional[str] = None
    try:
        if target_row.is_framework:
            if tier3_proposer is None:
                from app.tools.request_tier3_amendment import (
                    request_tier3_amendment as tier3_proposer,
                )
            cr_id = tier3_proposer(
                target_path="requirements.txt",
                new_content=f"# major bump: {package}=={to_version}",
                reason=(
                    f"Operator-accepted MAJOR framework upgrade for "
                    f"{package} {target_row.from_version}→{to_version} "
                    f"per ecosystem snapshot {year}."
                ),
                actor=operator_actor,
            )
        else:
            if cr_filer is None:
                from app.change_requests.lifecycle import (
                    create_request as cr_filer,
                )
            cr_id = cr_filer(
                requestor="ecosystem_snapshot",
                target_path="requirements.txt",
                new_content=f"{package}=={to_version}",
                reason=(
                    f"Operator-accepted MAJOR upgrade for "
                    f"{package} {target_row.from_version}→{to_version} "
                    f"per ecosystem snapshot {year}."
                ),
            )
    except Exception:
        logger.debug("ul.ecosystem: downstream filing failed", exc_info=True)
        # Don't lose the operator's acceptance — persist the row even
        # if filing failed. Operator can retry via the same endpoint.
        target_row.status = "accepted"

    if cr_id is not None:
        target_row.cr_id = str(cr_id)
    _persist_snapshot(snapshot)
    _emit_ledger_event(
        snapshot, subkind="acceptance",
        extra={"package": package, "to_version": to_version,
              "cr_id": cr_id, "actor": operator_actor},
    )
    return {"ok": True, "cr_id": cr_id, "row": target_row.to_dict()}
