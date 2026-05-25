"""Shared types for the upgrade-lifecycle subsystem.

Lives in its own module so U1 (changelog_fetcher), U2 (impact_analysis),
U3 (trial_runner), U5 (capability_adoption), and U6 (ecosystem_snapshot)
can import the dataclasses without circular imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Capability extraction (U1) ───────────────────────────────────────────


@dataclass(frozen=True)
class Capability:
    """One extracted upgrade — what changed between two versions of a package.

    Persisted as one JSONL row in
    ``workspace/upgrade_lifecycle/capabilities/<package>.jsonl``.

    All five list fields are short human-readable strings (no code).
    The LLM is instructed to produce one fact per list entry,
    consumable by impact_analysis (U2) regex-matching against call
    sites and by capability_adoption (U5) pattern detection.
    """

    package: str
    from_version: str
    to_version: str
    source: str                          # "pypi" | "github_releases" | "manual"
    extracted_at: str                    # ISO-8601 UTC
    new_features: tuple[str, ...] = ()
    deprecations: tuple[str, ...] = ()
    breaking_changes: tuple[str, ...] = ()
    security_fixes: tuple[str, ...] = ()
    perf_notes: tuple[str, ...] = ()
    # P2#c (PROGRAM §63.9) — license change detection. The LLM is
    # instructed to populate ``license_change`` with a short summary
    # ("BSD-3 → AGPLv3" or "added commercial-use restriction") when
    # the changelog explicitly mentions a license shift. Empty by
    # default. Surfaces in the radar's CR body so operator sees
    # legal/licensing risk before approving the bump.
    license_change: str = ""
    notes: str = ""                       # free-text follow-up (optional)
    raw_excerpt_sha256: str = ""          # hash of the changelog text the LLM saw

    def to_payload(self) -> dict[str, Any]:
        """Dict view suitable for JSON serialization / hashing.

        Sorted keys deterministically (see source_ledger pattern) so the
        same Capability always produces the same canonical bytes.
        """
        return {
            "package": self.package,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "source": self.source,
            "extracted_at": self.extracted_at,
            "new_features": list(self.new_features),
            "deprecations": list(self.deprecations),
            "breaking_changes": list(self.breaking_changes),
            "security_fixes": list(self.security_fixes),
            "perf_notes": list(self.perf_notes),
            "license_change": self.license_change,
            "notes": self.notes,
            "raw_excerpt_sha256": self.raw_excerpt_sha256,
        }


# ── Impact analysis (U2) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CallSite:
    """A specific file:line where the upgrading package is touched."""

    file_path: str
    line: int
    symbol: str                          # the dotted symbol referenced (e.g. "asyncio.gather")
    kind: str                            # "import" | "from_import" | "attribute"
    matched_capability: str = ""        # the deprecation / breaking-change string this matches


@dataclass
class ImpactReport:
    """Output of one ``analyze(package, capability)`` call.

    Designed so U4's MAJOR auto-CR gate can answer two questions:
    ``breaking_hits == 0`` (safe to auto-CR) and
    ``tier_immutable_touched`` (refused regardless of bump severity).
    """

    package: str
    from_version: str
    to_version: str
    call_sites: list[CallSite] = field(default_factory=list)
    deprecation_hits: int = 0
    breaking_hits: int = 0
    tier_immutable_touched: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "call_sites": [
                {
                    "file_path": s.file_path,
                    "line": s.line,
                    "symbol": s.symbol,
                    "kind": s.kind,
                    "matched_capability": s.matched_capability,
                }
                for s in self.call_sites
            ],
            "deprecation_hits": self.deprecation_hits,
            "breaking_hits": self.breaking_hits,
            "tier_immutable_touched": self.tier_immutable_touched,
        }


# ── Trial harness (U3) ───────────────────────────────────────────────────


@dataclass
class TrialResult:
    """Outcome of one upgrade-trial run.

    ``status``:
      * ``"ok"`` — full suite passed
      * ``"test_failure"`` — at least one test failed
      * ``"install_failure"`` — pip install failed
      * ``"timeout"`` — pytest exceeded the wallclock cap
      * ``"infrastructure_error"`` — worktree spin / cleanup error

    ``smoke_results`` (Gap 2) — per-smoke-runner verdicts. Each entry is
    a dict with at minimum ``{"name": str, "status": "ok"|"fail"|"error",
    "details": str}``. Runner-specific extra fields permitted. Empty when
    no smoke runners are configured for the package.
    """

    package: str
    from_version: str
    to_version: str
    status: str
    pass_count: int = 0
    fail_count: int = 0
    failures: tuple[str, ...] = ()       # capped to top-5 short descriptions
    elapsed_s: float = 0.0
    cost_estimate_usd: float = 0.0
    session_id: str = ""
    smoke_results: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "status": self.status,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "failures": list(self.failures),
            "elapsed_s": self.elapsed_s,
            "cost_estimate_usd": self.cost_estimate_usd,
            "session_id": self.session_id,
            "smoke_results": list(self.smoke_results),
        }
