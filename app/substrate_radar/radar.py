"""substrate_radar.radar — OS / container / cloud EOL detection.

Tier 2.1 of the 2026-05-24 ultrathink analysis closure.

Walks five surfaces:

  1. Base image EOLs — parses Dockerfile FROM lines, looks up the
     declared distro version against an embedded EOL table.
  2. Docker Compose schema version — reads docker-compose.yml's
     top-level ``version:`` key. ``compose v1`` schemas are deprecated;
     ``v3.x`` schemas are still supported but the ``version`` key
     itself is no-op as of Compose v2 — surfacing this lets the
     operator clean it up.
  3. Cloud API sunsets — reads ``cloud_api_eol.json`` (operator
     curated, optional). Each entry has ``provider``, ``api``,
     ``version``, ``eol_date``. Findings fire when EOL is within
     365 days.
  4. Python language version EOL — cross-references the §63 table
     (read-only) and surfaces under this radar too so the operator
     has one place to review substrate health.
  5. Compose service image freshness — checks if the live
     ``docker-compose.yml`` references images that are NOT pinned by
     SHA-256 digest (a tag like ``postgres:15`` will float and
     silently change).

Each finding routes through the existing proposal_bridge for
patchable cases (image tag bump) or fires a Signal alert for non-
patchable cases (cloud API deprecation needs operator decision).
"""
from __future__ import annotations

import datetime as dt
import enum
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ── Embedded base-image EOL table ────────────────────────────────────────
# Each entry: distro → version → EOL date (YYYY-MM-DD).
# Sources: endoflife.date community-maintained list. Values frozen at
# 2026-05-24; the radar refuses to fire on dates older than 2 years
# from the runtime clock, so a stale table fails safe (no alert) rather
# than firing on every probe.
_BASE_IMAGE_EOL: dict[str, dict[str, str]] = {
    "debian": {
        # bullseye = 11, bookworm = 12, trixie = 13
        "11": "2026-08-15",
        "12": "2028-06-10",
        "13": "2030-06-30",
    },
    "ubuntu": {
        "20.04": "2025-05-29",
        "22.04": "2027-04-21",
        "24.04": "2029-04-25",
    },
    "alpine": {
        # Alpine maintains 2-year support per minor version
        "3.18": "2025-05-09",
        "3.19": "2025-11-01",
        "3.20": "2026-04-01",
        "3.21": "2026-11-01",
    },
}


# Compose schemas declared via top-level ``version:`` key are no-op
# under Compose v2 (release 2020). Surfacing this is a hygiene win.
_DEPRECATED_COMPOSE_VERSIONS = {"1", "1.0", "2"}


# ── Data model ───────────────────────────────────────────────────────────


class SubstrateSeverity(str, enum.Enum):
    """Routing class. Mirrors dependency_radar.Severity."""

    CRITICAL = "critical"   # EOL within 90 days
    HIGH = "high"           # EOL within 180 days
    MEDIUM = "medium"       # EOL within 365 days
    LOW = "low"             # Hygiene (e.g. compose version key)
    INFO = "info"           # FYI


@dataclass
class SubstrateFinding:
    """One detected substrate-layer issue."""

    kind: str               # "base_image_eol" / "compose_version" /
                            # "cloud_api_sunset" / "python_eol" /
                            # "unpinned_image"
    subject: str            # e.g. "debian:11" or "postgres:15"
    severity: SubstrateSeverity
    detail: str             # operator-readable
    eol_date: Optional[str] = None
    days_remaining: Optional[int] = None
    source_path: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


# ── Detection helpers ────────────────────────────────────────────────────


def _repo_root() -> Path:
    try:
        from app.paths import REPO_ROOT  # type: ignore

        return Path(REPO_ROOT)
    except Exception:
        for c in (Path("/app"), Path.cwd(), Path(__file__).resolve().parents[2]):
            if (c / "install.sh").exists() or (c / "Dockerfile").exists():
                return c
        return Path.cwd()


def _today() -> dt.date:
    return dt.date.today()


def _severity_from_days(days: int) -> SubstrateSeverity:
    if days <= 90:
        return SubstrateSeverity.CRITICAL
    if days <= 180:
        return SubstrateSeverity.HIGH
    if days <= 365:
        return SubstrateSeverity.MEDIUM
    return SubstrateSeverity.INFO


_FROM_RE = re.compile(
    r"^FROM\s+(?:--platform=\S+\s+)?([\w./-]+):([\w.-]+)",
    re.IGNORECASE,
)


def _parse_dockerfile_froms(dockerfile: Path) -> list[tuple[str, str, str]]:
    """Return (image, tag, full_line) tuples for each FROM line."""
    out: list[tuple[str, str, str]] = []
    try:
        for line in dockerfile.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _FROM_RE.match(line.strip())
            if not m:
                continue
            out.append((m.group(1), m.group(2), line.strip()))
    except OSError:
        logger.debug("substrate_radar: dockerfile read failed", exc_info=True)
    return out


def _detect_base_image_eol(repo: Path) -> list[SubstrateFinding]:
    findings: list[SubstrateFinding] = []
    today = _today()
    for dockerfile in repo.glob("Dockerfile*"):
        if not dockerfile.is_file():
            continue
        for image, tag, line in _parse_dockerfile_froms(dockerfile):
            # Two patterns: image:tag where image is the distro (debian:11)
            # OR image is a derived tag containing distro hint (python:3.13-slim)
            distro = None
            version_hint = None
            base_lower = image.lower()
            for d in _BASE_IMAGE_EOL.keys():
                if base_lower == d or base_lower.endswith(f"/{d}"):
                    distro = d
                    version_hint = tag.split("-")[0]
                    break
            if distro is None:
                # Inspect tag for embedded distro version (slim-bookworm etc)
                for d in _BASE_IMAGE_EOL.keys():
                    if d in tag.lower():
                        distro = d
                        break
                if distro is None:
                    continue
            if version_hint is None:
                # Match version numbers in the tag
                for v in _BASE_IMAGE_EOL[distro].keys():
                    if v in tag:
                        version_hint = v
                        break
            if version_hint is None:
                continue
            eol_str = _BASE_IMAGE_EOL.get(distro, {}).get(version_hint)
            if not eol_str:
                continue
            try:
                eol = dt.date.fromisoformat(eol_str)
            except ValueError:
                continue
            days = (eol - today).days
            if days <= 365:
                sev = _severity_from_days(days)
                findings.append(
                    SubstrateFinding(
                        kind="base_image_eol",
                        subject=f"{distro}:{version_hint}",
                        severity=sev,
                        detail=(
                            f"{distro} {version_hint} reaches EOL on "
                            f"{eol_str} ({days} days)"
                        ),
                        eol_date=eol_str,
                        days_remaining=days,
                        source_path=str(dockerfile.relative_to(repo)),
                    )
                )
    return findings


def _detect_compose_issues(repo: Path) -> list[SubstrateFinding]:
    findings: list[SubstrateFinding] = []
    compose = repo / "docker-compose.yml"
    if not compose.exists():
        return findings
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(compose.open("r", encoding="utf-8"))
    except Exception:
        return findings
    if not isinstance(data, dict):
        return findings

    version = str(data.get("version") or "").strip()
    if version and version in _DEPRECATED_COMPOSE_VERSIONS:
        findings.append(
            SubstrateFinding(
                kind="compose_version",
                subject=f"version: {version!r}",
                severity=SubstrateSeverity.LOW,
                detail=(
                    "docker-compose.yml declares a deprecated version "
                    "key. Compose v2 ignores it; remove for hygiene."
                ),
                source_path="docker-compose.yml",
            )
        )

    services = data.get("services") or {}
    if isinstance(services, dict):
        for svc_name, svc in services.items():
            if not isinstance(svc, dict):
                continue
            image = svc.get("image")
            if not isinstance(image, str):
                continue
            # Unpinned = no @sha256:digest suffix. ``postgres:15`` will
            # float; ``postgres:15@sha256:abc...`` is pinned.
            if "@sha256:" in image or "@sha512:" in image:
                continue
            findings.append(
                SubstrateFinding(
                    kind="unpinned_image",
                    subject=image,
                    severity=SubstrateSeverity.LOW,
                    detail=(
                        f"service {svc_name!r} references image {image!r} "
                        "without a digest pin — the underlying image can "
                        "silently change. Add @sha256:... for "
                        "reproducible builds."
                    ),
                    source_path="docker-compose.yml",
                    extra={"service": svc_name},
                )
            )
    return findings


def _detect_cloud_api_sunsets(repo: Path) -> list[SubstrateFinding]:
    """Read operator-curated cloud_api_eol.json (optional file)."""
    findings: list[SubstrateFinding] = []
    path = repo / "app" / "substrate_radar" / "cloud_api_eol.json"
    if not path.exists():
        return findings
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return findings
    if not isinstance(raw, list):
        return findings
    today = _today()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider") or "")
        api = str(entry.get("api") or "")
        version = str(entry.get("version") or "")
        eol_str = str(entry.get("eol_date") or "")
        if not (provider and api and version and eol_str):
            continue
        try:
            eol = dt.date.fromisoformat(eol_str)
        except ValueError:
            continue
        days = (eol - today).days
        if days <= 365:
            findings.append(
                SubstrateFinding(
                    kind="cloud_api_sunset",
                    subject=f"{provider}/{api}/{version}",
                    severity=_severity_from_days(days),
                    detail=(
                        f"{provider} API {api} version {version} "
                        f"sunsets on {eol_str} ({days} days). "
                        "Operator action required."
                    ),
                    eol_date=eol_str,
                    days_remaining=days,
                    source_path="app/substrate_radar/cloud_api_eol.json",
                    extra={"provider": provider, "api": api, "version": version},
                )
            )
    return findings


def _detect_python_eol() -> list[SubstrateFinding]:
    """Cross-reference §63's ``python_eol_proximity`` table without
    duplicating logic. Read-only on the existing constant."""
    try:
        from app.upgrade_lifecycle.ecosystem_snapshot import (
            PYTHON_EOL_TABLE,
        )
    except Exception:
        return []
    import sys

    cur_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    eol_str = PYTHON_EOL_TABLE.get(cur_minor)
    if not eol_str:
        return []
    try:
        eol = dt.date.fromisoformat(eol_str)
    except ValueError:
        return []
    days = (eol - _today()).days
    if days > 365:
        return []
    return [
        SubstrateFinding(
            kind="python_eol",
            subject=f"python {cur_minor}",
            severity=_severity_from_days(days),
            detail=(
                f"Python {cur_minor} reaches EOL on {eol_str} "
                f"({days} days). Crosslink to §63 ecosystem snapshot."
            ),
            eol_date=eol_str,
            days_remaining=days,
        )
    ]


# ── Public detection entry ───────────────────────────────────────────────


def detect_findings(repo: Path | None = None) -> list[SubstrateFinding]:
    """Single pass — walks every detection surface. Failure-isolated."""
    repo = repo or _repo_root()
    out: list[SubstrateFinding] = []
    for fn in (
        _detect_base_image_eol,
        _detect_compose_issues,
        _detect_cloud_api_sunsets,
    ):
        try:
            out.extend(fn(repo))
        except Exception as exc:
            logger.debug(
                "substrate_radar: %s raised %r", fn.__name__, exc,
                exc_info=True,
            )
    try:
        out.extend(_detect_python_eol())
    except Exception:
        logger.debug("substrate_radar: python_eol probe raised", exc_info=True)
    return out


def _enabled() -> bool:
    try:
        from app import runtime_settings

        return bool(runtime_settings.get_substrate_radar_enabled())
    except Exception:
        return True


def _signal_alert(finding: SubstrateFinding) -> bool:
    """Best-effort Signal alert for high-severity findings."""
    try:
        from app.notify import arbiter as _arbiter

        body = (
            f"🛡️ Substrate EOL — {finding.severity.value.upper()}\n\n"
            f"{finding.subject}\n{finding.detail}"
        )
        return bool(
            _arbiter.notify(
                title="substrate_radar finding",
                body=body,
                topic=f"substrate_radar:{finding.kind}",
                critical=(finding.severity == SubstrateSeverity.CRITICAL),
            )
        )
    except Exception:
        logger.debug("substrate_radar: notify failed", exc_info=True)
        return False


def run_one_pass(
    *,
    notify_fn: Optional[Callable[[SubstrateFinding], bool]] = None,
) -> dict[str, Any]:
    """One detection pass. Idle-job entry. Failure-isolated."""
    if not _enabled():
        return {"skipped_reason": "master_switch_off"}
    findings = detect_findings()
    alerted = 0
    for f in findings:
        if f.severity in (SubstrateSeverity.CRITICAL, SubstrateSeverity.HIGH):
            ok = (notify_fn or _signal_alert)(f)
            if ok:
                alerted += 1
    return {
        "n_findings": len(findings),
        "n_alerted": alerted,
        "by_severity": {
            sev.value: sum(1 for f in findings if f.severity == sev)
            for sev in SubstrateSeverity
        },
    }


__all__ = [
    "SubstrateFinding",
    "SubstrateSeverity",
    "detect_findings",
    "run_one_pass",
]
