"""U1 — Capability extraction.

PROGRAM §63 — Stage B of the upgrade lifecycle. Reads release metadata
+ release notes for an outdated package and asks the LLM to structure
the delta into a :class:`Capability` row (new_features / deprecations /
breaking_changes / security_fixes / perf_notes).

Composes with — does not replace — :mod:`app.dependency_radar`. The
radar discovers WHICH packages are outdated; this module produces
the structured WHAT-CHANGED data the rest of the lifecycle needs.

Persistence is a per-package hash-chained JSONL at
``workspace/upgrade_lifecycle/capabilities/<package>.jsonl`` —
mirrors the source-ledger pattern (PROGRAM §56) so a bit-rot scan or
chain-verification check works on these files exactly like every
other audit-grade JSONL in the project.

All LLM model decisions go through :func:`app.llm_factory.create_specialist_llm`.
No model IDs are hardcoded in this file.

Failure-isolated end to end: every public function is wrapped in
try/except so a transient PyPI / GitHub / LLM hiccup never raises
out of the daemon loop.
"""
from __future__ import annotations

import hashlib
import html.parser as html_parser
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app.upgrade_lifecycle.protocol import Capability

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


_PYPI_BASE = "https://pypi.org/pypi"
_GITHUB_API = "https://api.github.com"
_REQUEST_TIMEOUT_S = 30
_MAX_CHANGELOG_CHARS = 24_000        # cap LLM input to keep extraction cheap
_MAX_EXCERPT_FOR_HASH = 32_768        # hash up to this much of the source text
_GENESIS_HASH = "0" * 64
_USER_AGENT = "AndrusAI-UpgradeLifecycle/1.0"
# P1#c — Per-extraction cost estimate. PyPI metadata fetches +
# GitHub releases + a 4096-token LLM call run ≈ $0.05-0.20/version
# (Anthropic Haiku-class). We use $0.10 as the per-call charge
# against the monthly budget; the budget itself is operator-set in
# runtime_settings.
_ESTIMATED_COST_PER_EXTRACTION_USD = 0.10

# Framework-level packages — never auto-routed through U4's MAJOR auto-CR
# gate, but capability extraction itself is harmless and useful for the
# annual ecosystem snapshot (U6). The exclusion lives here as the
# authoritative copy; U4 re-imports.
FRAMEWORK_PACKAGES = frozenset({
    "crewai",
    "chromadb",
    "fastapi",
    "pydantic",
    "pydantic-settings",
    "starlette",
    "anthropic",
})


# ── Connector-budget integration ──────────────────────────────────────────


try:
    from app.connector_budget import (
        ConnectorBudgetExceeded as _BudgetExceeded,
        with_connector_budget as _with_budget,
    )

    @_with_budget(connector="upgrade_lifecycle_pypi", daily_call_cap=200)
    def _budgeted_get(url: str, timeout: int) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    @_with_budget(connector="upgrade_lifecycle_github", daily_call_cap=300)
    def _budgeted_github_get(url: str, timeout: int) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    @_with_budget(connector="upgrade_lifecycle_changelog", daily_call_cap=200)
    def _budgeted_changelog_get(url: str, timeout: int) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    _BUDGET_AVAILABLE = True
except Exception:
    _BUDGET_AVAILABLE = False

    class _BudgetExceeded(Exception):  # type: ignore[no-redef]
        pass

    def _budgeted_get(url: str, timeout: int) -> bytes:  # type: ignore[no-redef]
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _budgeted_github_get(url: str, timeout: int) -> bytes:  # type: ignore[no-redef]
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _budgeted_changelog_get(url: str, timeout: int) -> bytes:  # type: ignore[no-redef]
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()


# ── Paths + master switch ────────────────────────────────────────────────


def _capabilities_dir() -> Path:
    """Per-package ledger directory.

    Honors ``UPGRADE_LIFECYCLE_DIR`` env override so tests can
    redirect without touching production state.
    """
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / "capabilities"
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / "capabilities"
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle/capabilities")


def _ledger_path(package: str) -> Path:
    """One JSONL per package — keeps each file small enough to bit-rot-scan."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", package.lower())
    return _capabilities_dir() / f"{safe}.jsonl"


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_upgrade_lifecycle_capability_extraction_enabled
        return get_upgrade_lifecycle_capability_extraction_enabled()
    except Exception:
        return True   # default ON until the runtime setter lands


# ── P1#c — Monthly LLM budget ────────────────────────────────────────────


def _monthly_budget_usd() -> float:
    try:
        from app.runtime_settings import (
            get_upgrade_lifecycle_extraction_budget_usd_monthly,
        )
        return float(get_upgrade_lifecycle_extraction_budget_usd_monthly())
    except Exception:
        return 5.0


def _budget_ledger_path() -> Path:
    """Per-month spend tracking for U1 extraction LLM calls."""
    return _capabilities_dir().parent / "extraction_budget_ledger.jsonl"


def _current_month_key(now: Optional[datetime] = None) -> str:
    """``"YYYY-MM"`` calendar month — matches U5's quarter pattern."""
    dt = now or datetime.now(timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def current_month_extraction_spend(now: Optional[datetime] = None) -> float:
    """Sum the per-extraction cost rows for the current calendar month."""
    mk = _current_month_key(now)
    path = _budget_ledger_path()
    if not path.exists():
        return 0.0
    total = 0.0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("month") or "") == mk:
                    total += float(row.get("cost_usd") or 0.0)
    except OSError:
        return 0.0
    return total


def remaining_month_extraction_budget(now: Optional[datetime] = None) -> float:
    """Operator-visible budget headroom; consumed at gate-time below."""
    return max(0.0, _monthly_budget_usd() - current_month_extraction_spend(now=now))


def _record_extraction_attempt(
    *,
    package: str, to_version: str, succeeded: bool,
    now: Optional[datetime] = None,
) -> None:
    """Append a per-attempt cost row (always charged, success or failure —
    the LLM is paid for the call, not for usable output)."""
    dt = now or datetime.now(timezone.utc)
    path = _budget_ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "month": _current_month_key(dt),
                "ts": dt.isoformat(),
                "package": package,
                "to_version": to_version,
                "cost_usd": _ESTIMATED_COST_PER_EXTRACTION_USD,
                "succeeded": bool(succeeded),
            }, sort_keys=True) + "\n")
    except OSError:
        logger.debug("ul.u1: budget ledger write failed", exc_info=True)


# ── Hash chain (mirrors source_ledger pattern) ───────────────────────────


def _canonical_json(payload: dict) -> str:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def _compute_row_hash(prev_hash: str, payload: dict) -> str:
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(_canonical_json(payload).encode("utf-8"))
    return h.hexdigest()


def _last_hash_for(package: str) -> str:
    """Read the chain tip for *package*, or GENESIS on empty / missing file."""
    path = _ledger_path(package)
    if not path.exists():
        return _GENESIS_HASH
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return _GENESIS_HASH
            # Walk back to find the start of the last line.
            block = min(4096, size)
            f.seek(size - block, os.SEEK_SET)
            tail = f.read(block).decode("utf-8", errors="replace")
        last_nl = tail.rfind("\n", 0, len(tail) - 1)
        last_line = tail[last_nl + 1:].strip() if last_nl >= 0 else tail.strip()
        if not last_line:
            return _GENESIS_HASH
        return str(json.loads(last_line).get("hash") or _GENESIS_HASH)
    except Exception:
        logger.debug("ul: tip-read failed for %s", package, exc_info=True)
        return _GENESIS_HASH


# ── PyPI + GitHub fetchers ───────────────────────────────────────────────


def _fetch_pypi_metadata(package: str) -> Optional[dict[str, Any]]:
    """``GET /pypi/<pkg>/json``. Returns the parsed JSON or None on failure.

    A5-P1 (PROGRAM §63.11) — on failure, falls through to
    :func:`_fetch_pypi_metadata_via_github`. Decade-scale, pypi.org
    might change its JSON API or sunset; the system retains
    capability extraction via the GitHub fallback for any package
    whose repo URL we've previously cached.
    """
    url = f"{_PYPI_BASE}/{urllib.parse.quote(package)}/json"
    try:
        body = _budgeted_get(url, timeout=_REQUEST_TIMEOUT_S)
        result = json.loads(body.decode("utf-8"))
        # Side effect: cache the GitHub repo URL for fallback use.
        _maybe_cache_github_repo(package, result)
        return result
    except (_BudgetExceeded, urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError):
        return _fetch_pypi_metadata_via_github(package)
    except Exception:
        logger.debug("ul: pypi metadata fetch failed for %s", package, exc_info=True)
        return _fetch_pypi_metadata_via_github(package)


# ── A5-P1: GitHub fallback for PyPI-down scenarios ──────────────────────


def _repo_cache_path() -> Path:
    """Per-package github-repo cache so the fallback works after PyPI dies."""
    override = os.getenv("UPGRADE_LIFECYCLE_DIR")
    if override:
        return Path(override) / "github_repo_cache.json"
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT) / "upgrade_lifecycle" / "github_repo_cache.json"
    except Exception:
        return Path("/app/workspace/upgrade_lifecycle/github_repo_cache.json")


def _maybe_cache_github_repo(package: str, pypi_metadata: Optional[dict]) -> None:
    """When PyPI succeeds, persist (package → owner/repo) so the
    GitHub fallback can find the repo if PyPI later fails."""
    if not pypi_metadata:
        return
    ownerrepo = _github_owner_repo(pypi_metadata)
    if not ownerrepo:
        return
    path = _repo_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, str] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        existing[package.lower()] = f"{ownerrepo[0]}/{ownerrepo[1]}"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2, sort_keys=True))
        tmp.replace(path)
    except OSError:
        logger.debug("ul: github repo cache write failed", exc_info=True)


def _cached_github_repo(package: str) -> Optional[tuple[str, str]]:
    path = _repo_cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    val = data.get(package.lower())
    if not val or "/" not in val:
        return None
    owner, _, repo = val.partition("/")
    return owner, repo


def _fetch_pypi_metadata_via_github(package: str) -> Optional[dict[str, Any]]:
    """Reconstruct a minimal PyPI-shaped dict from GitHub's release API.

    Only the fields downstream code uses are populated:
      * ``info.project_urls.Source`` for ``_github_owner_repo``
      * ``releases[<tag>][0].upload_time`` for U4's 30d window check

    Returns None when we have no cached repo for *package* — first-time
    discovery still requires PyPI. Once PyPI has been queried successfully
    even once, this fallback covers subsequent extractions even if
    PyPI is unreachable.
    """
    ownerrepo = _cached_github_repo(package)
    if not ownerrepo:
        return None
    owner, repo = ownerrepo
    releases = _fetch_github_releases(owner, repo, limit=30)
    if not releases:
        return None

    # Synthesize the PyPI shape. Each release becomes a single-element
    # array under ``releases[<tag>]`` with the ``published_at``
    # timestamp re-labelled ``upload_time``.
    synth_releases: dict[str, list[dict[str, str]]] = {}
    for rel in releases:
        tag = _normalize_version(str(rel.get("tag_name") or rel.get("name") or ""))
        published = rel.get("published_at") or ""
        if not tag or not published:
            continue
        # PyPI's upload_time format is naive ISO without trailing Z.
        upload_time = published.rstrip("Z")
        synth_releases.setdefault(tag, []).append({
            "upload_time": upload_time,
        })

    return {
        "info": {
            "project_urls": {"Source": f"https://github.com/{owner}/{repo}"},
            "description": "",
            "_synthesized_from": "github_releases",
        },
        "releases": synth_releases,
    }


_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git|/|$)",
)


def _github_owner_repo(pypi_metadata: dict[str, Any]) -> Optional[tuple[str, str]]:
    """Walk PyPI's ``project_urls`` + ``home_page`` for a GitHub link."""
    info = pypi_metadata.get("info") or {}
    candidates: list[str] = []
    for key in ("home_page", "project_url", "package_url"):
        v = info.get(key)
        if isinstance(v, str):
            candidates.append(v)
    project_urls = info.get("project_urls") or {}
    if isinstance(project_urls, dict):
        for v in project_urls.values():
            if isinstance(v, str):
                candidates.append(v)
    for url in candidates:
        m = _GITHUB_URL_RE.search(url)
        if m:
            owner, repo = m.group(1), m.group(2)
            if repo.endswith(".git"):
                repo = repo[:-4]
            return owner, repo
    return None


def _fetch_github_releases(owner: str, repo: str, *, limit: int = 30) -> list[dict[str, Any]]:
    """``GET /repos/{owner}/{repo}/releases?per_page=30``. Empty list on failure."""
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/releases?per_page={limit}"
    try:
        body = _budgeted_github_get(url, timeout=_REQUEST_TIMEOUT_S)
        data = json.loads(body.decode("utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (_BudgetExceeded, urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError):
        return []
    except Exception:
        logger.debug("ul: github releases fetch failed for %s/%s", owner, repo, exc_info=True)
        return []


def _normalize_version(raw: str) -> str:
    """Strip leading ``v`` and surrounding whitespace.

    Both PyPI and GitHub release tags vary (``1.0.0`` vs ``v1.0.0``);
    the lifecycle compares normalized forms.
    """
    return raw.strip().lstrip("vV")


def _releases_between(
    releases: list[dict[str, Any]],
    *,
    from_version: str,
    to_version: str,
) -> list[dict[str, Any]]:
    """Filter GitHub releases to those between (exclusive) from + (inclusive) to.

    Returns chronological-oldest-first so the LLM sees the changelog
    in the order a human reading the release page would.
    """
    fv = _normalize_version(from_version)
    tv = _normalize_version(to_version)
    selected: list[dict[str, Any]] = []
    for rel in releases:
        tag = _normalize_version(str(rel.get("tag_name") or rel.get("name") or ""))
        if not tag:
            continue
        # Inclusive of to_version, exclusive of from_version.
        if tag == fv:
            continue
        if tag == tv or _version_le(tag, tv):
            selected.append(rel)
    selected.sort(key=lambda r: r.get("published_at") or "")
    return selected


def _version_le(a: str, b: str) -> bool:
    """Pessimistic version comparison — treats unparseable parts as 0.

    Good enough for filtering release lists; the LLM doesn't care
    about edge cases like RC suffixes.
    """
    def _parts(v: str) -> tuple[int, ...]:
        out: list[int] = []
        for chunk in re.split(r"[._-]", v):
            try:
                out.append(int(chunk))
            except ValueError:
                break
        return tuple(out)
    return _parts(a) <= _parts(b)


# ── LLM extraction (via factory — no hardcoded model IDs) ────────────────


_LLM_SYSTEM_PROMPT = """You analyse software-release changelogs.

Given the changelog text between two versions of a Python package,
extract a structured delta. Output STRICT JSON. No prose outside
the JSON.

Schema:

  {
    "new_features": ["short factual entries; one capability each"],
    "deprecations": ["API X is deprecated; replace with Y"],
    "breaking_changes": ["A.B.C was removed; signature of X changed"],
    "security_fixes": ["CVE-YYYY-NNNN: short summary"],
    "perf_notes": ["X is N times faster; Y memory footprint reduced"],
    "license_change": "single-line summary if the project license changed (e.g. 'BSD-3 → AGPLv3'). Empty string if no license change is mentioned in the changelog.",
    "notes": "single-paragraph free-text caveat or context (optional)"
  }

Rules:
  * Concrete, factual, citable from the source text.
  * No marketing fluff ("better", "improved" without specifics).
  * If the text is too thin to extract anything, return all empty arrays.
  * Keep each list entry under 200 characters.
"""


def _assemble_excerpt(
    *,
    package: str,
    from_version: str,
    to_version: str,
    pypi_metadata: Optional[dict[str, Any]],
    github_releases: list[dict[str, Any]],
    changelog_section: Optional[str] = None,
) -> tuple[str, str]:
    """Build the changelog text the LLM will read.

    Returns ``(text, source_label)`` where source_label reflects the
    most authoritative source we found:

      * ``"changelog_url"`` — project CHANGELOG.md sliced to the version
        range (Gap 5 third adapter; often the richest signal for
        packages with a curated changelog).
      * ``"github_releases"`` — GitHub release bodies.
      * ``"pypi"`` — PyPI ``description`` field.

    When multiple sources are present, the changelog section is
    PREPENDED to the GitHub release bodies (LLM sees both). The label
    reports the most authoritative source actually included.
    """
    chunks: list[str] = [f"# {package} {from_version} → {to_version}"]
    source_label = "pypi"
    have_content = False
    if changelog_section and changelog_section.strip():
        chunks.append("\n## project changelog\n")
        chunks.append(changelog_section.strip())
        source_label = "changelog_url"
        have_content = True
    if github_releases:
        if source_label == "pypi":
            source_label = "github_releases"
        for rel in github_releases:
            tag = rel.get("tag_name") or rel.get("name") or "?"
            published = rel.get("published_at") or ""
            body = (rel.get("body") or "").strip()
            chunks.append(f"\n## release {tag}  ({published})")
            if body:
                chunks.append(body)
        have_content = True
    if not have_content and pypi_metadata:
        info = pypi_metadata.get("info") or {}
        desc = (info.get("description") or "").strip()
        if desc:
            chunks.append("\n## pypi description\n")
            chunks.append(desc)
    text = "\n".join(chunks)
    if len(text) > _MAX_CHANGELOG_CHARS:
        text = text[:_MAX_CHANGELOG_CHARS] + "\n\n[...truncated...]"
    return text, source_label


# ── Gap 5: CHANGELOG.md URL adapter (third content source) ──────────────


class _ChangelogTextExtractor(html_parser.HTMLParser):
    """Strip HTML tags, preserving text content and inserting newlines
    for block-level elements so heading slicing still works on rendered
    pages. Stdlib-only — no BeautifulSoup dependency."""

    _BLOCK_TAGS = frozenset({
        "p", "br", "div", "section", "article", "li", "ul", "ol",
        "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "tr",
    })

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            # Re-emit a markdown-style heading marker so the version
            # slicer can find boundaries on rendered HTML.
            level = int(tag[1])
            self._parts.append("\n" + ("#" * level) + " ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        joined = "".join(self._parts)
        # Collapse 3+ consecutive newlines to 2 (paragraph break) and
        # leading whitespace on each line trimmed.
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        joined = "\n".join(line.rstrip() for line in joined.splitlines())
        return joined.strip()


def _strip_html(text: str) -> str:
    """Return the textual content of an HTML document, preserving
    semantic block boundaries (so version-heading slicing still works)."""
    parser = _ChangelogTextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        # html.parser is permissive but not infallible — on parse error
        # fall back to a naive tag-strip rather than dropping the source.
        return re.sub(r"<[^>]+>", "", text)
    return parser.get_text()


_VERSION_HEADING_RE = re.compile(
    # Match common changelog heading shapes:
    #   ## 1.2.3
    #   ## v1.2.3
    #   ## [1.2.3]
    #   ## 1.2.3 (2026-01-01)
    #   1.2.3
    #   -----
    # The version capture is the numeric core; the ``v`` prefix and any
    # surrounding brackets are absorbed by the alternation.
    r"^(?:#{1,6}\s+)?\[?v?(?P<ver>\d+(?:\.\d+){1,3})\]?(?:\s|$|\(|—|-)",
    re.MULTILINE,
)


def _slice_changelog_versions(
    text: str, from_version: str, to_version: str,
) -> str:
    """Slice the changelog text to the section bracketing ``from_version``
    (exclusive) and ``to_version`` (inclusive).

    Returns an empty string when no heading for ``to_version`` is found
    — in that case the whole text would be misleading, so we decline
    and the LLM gets PyPI + GitHub material only.
    """
    if not text.strip():
        return ""
    needle_to = _normalize_version(to_version)
    needle_from = _normalize_version(from_version)

    # Collect every (position, normalized_version) heading occurrence.
    matches: list[tuple[int, str]] = []
    for m in _VERSION_HEADING_RE.finditer(text):
        matches.append((m.start(), _normalize_version(m.group("ver"))))
    if not matches:
        return ""

    # Find the first heading that matches `to_version`.
    to_idx: Optional[int] = None
    for i, (_, ver) in enumerate(matches):
        if ver == needle_to:
            to_idx = i
            break
    if to_idx is None:
        return ""

    # The slice runs from the to_version heading downward through every
    # heading until (but not including) the from_version heading. If
    # from_version is not found, take everything from to_idx to the end
    # — better to over-include than under-include for fresh-cut releases.
    end_pos: Optional[int] = None
    for i in range(to_idx + 1, len(matches)):
        if matches[i][1] == needle_from:
            end_pos = matches[i][0]
            break
    start = matches[to_idx][0]
    section = text[start:end_pos] if end_pos is not None else text[start:]
    return section.strip()


def _changelog_url_from_pypi(
    pypi_metadata: Optional[dict[str, Any]],
) -> Optional[str]:
    """Extract the project-declared CHANGELOG URL from PyPI metadata.

    PEP 621 standardises ``project.urls`` (PyPI exposes via
    ``info.project_urls``). Maintainers commonly use ``Changelog``,
    ``Changes``, ``Release Notes``, or ``History`` — we accept any of
    those keys case-insensitively, since enforcing one canonical name
    would miss many projects."""
    if not pypi_metadata:
        return None
    info = pypi_metadata.get("info") or {}
    urls = info.get("project_urls") or {}
    if not isinstance(urls, dict):
        return None
    accepted_keys = ("changelog", "changes", "release notes",
                     "release-notes", "history")
    for key, val in urls.items():
        if isinstance(key, str) and key.strip().lower() in accepted_keys:
            if isinstance(val, str) and val.startswith(("http://", "https://")):
                return val
    return None


def _fetch_changelog_section(
    pypi_metadata: Optional[dict[str, Any]],
    from_version: str,
    to_version: str,
) -> Optional[str]:
    """Fetch the project CHANGELOG (when declared) and slice it to the
    version range. Returns None on any failure path — adapter never
    blocks the rest of the pipeline.

    Failure modes (each silent, recoverable):
      * No Changelog URL in project_urls
      * URL fetch blocked by connector budget
      * URL fetch raises (404, timeout, DNS)
      * No version heading matches ``to_version``
    """
    url = _changelog_url_from_pypi(pypi_metadata)
    if not url:
        return None
    try:
        body = _budgeted_changelog_get(url, timeout=_REQUEST_TIMEOUT_S)
    except (_BudgetExceeded, urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError):
        return None
    except Exception:
        logger.debug("ul: changelog fetch failed for %s", url, exc_info=True)
        return None
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        return None
    # HTML pages (most readthedocs / sphinx sites) require tag-strip;
    # plain markdown passes through. We detect with a permissive sniff.
    sniff = text[:1024].lower()
    if "<html" in sniff or "<!doctype html" in sniff or "<body" in sniff:
        text = _strip_html(text)
    section = _slice_changelog_versions(text, from_version, to_version)
    if not section:
        return None
    return section


def _extract_with_llm(
    *,
    excerpt: str,
    package: str,
    from_version: str,
    to_version: str,
    llm_builder: Optional[Callable[[], Any]] = None,
    system_prompt_suffix: str = "",
) -> Optional[dict[str, Any]]:
    """Issue the LLM call via the factory. Returns parsed dict or None.

    ``llm_builder`` is injectable for tests so they can return a
    deterministic stub instead of hitting the real factory + network.

    ``system_prompt_suffix`` is appended to the system prompt — used by
    the linter retry path to strengthen the constraint after a HARD_FAIL.
    """
    try:
        if llm_builder is None:
            from app.llm_factory import create_specialist_llm
            llm = create_specialist_llm(
                max_tokens=4096,
                role="research",
                task_hint="upgrade-lifecycle capability extraction",
            )
        else:
            llm = llm_builder()
    except Exception:
        logger.debug("ul: llm factory unavailable", exc_info=True)
        return None

    system_prompt = _LLM_SYSTEM_PROMPT
    if system_prompt_suffix:
        system_prompt = f"{system_prompt}\n\n{system_prompt_suffix}"

    user_msg = (
        f"package: {package}\nfrom_version: {from_version}\n"
        f"to_version: {to_version}\n\n"
        f"=== changelog text ===\n{excerpt}"
    )
    try:
        raw = str(llm.call(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ]
        )).strip()
    except Exception:
        logger.debug("ul: llm call failed", exc_info=True)
        return None
    return _parse_strict_json(raw)


# ── Phenomenal-language discipline at the producer (Gap 3) ──────────────


_TEXT_FIELDS_LIST = ("new_features", "deprecations", "breaking_changes",
                     "security_fixes", "perf_notes")
_TEXT_FIELDS_STR = ("license_change", "notes")


def _iter_text_fragments(parsed: dict[str, Any]) -> list[tuple[str, str]]:
    """Flatten the LLM-parsed dict into ``(field, fragment)`` pairs for
    linting. Each list entry becomes its own fragment so a HARD_FAIL on
    one entry doesn't taint sibling entries."""
    fragments: list[tuple[str, str]] = []
    for field in _TEXT_FIELDS_LIST:
        for entry in parsed.get(field) or ():
            if isinstance(entry, str) and entry.strip():
                fragments.append((field, entry))
    for field in _TEXT_FIELDS_STR:
        val = parsed.get(field)
        if isinstance(val, str) and val.strip():
            fragments.append((field, val))
    return fragments


def _lint_extraction(parsed: dict[str, Any]) -> tuple[set[str], list]:
    """Return ``(failing_fields, violations)``.

    A field is "failing" if any of its fragments HARD_FAILs the linter.
    The caller decides whether to retry the LLM or blank the failing
    fields. Failure-isolated: if the linter module isn't importable,
    return (empty set, empty list) so extraction proceeds.
    """
    try:
        from app.subia.inquiry.linter import PhenomenalLanguageLinter
    except Exception:
        return set(), []
    linter = PhenomenalLanguageLinter()
    failing: set[str] = set()
    all_violations: list = []
    for field, text in _iter_text_fragments(parsed):
        try:
            result = linter.lint(text)
        except Exception:
            continue
        if not result.ok:
            failing.add(field)
            all_violations.extend(result.hard_fails)
    return failing, all_violations


def _blank_failing_fields(parsed: dict[str, Any],
                          failing: set[str]) -> dict[str, Any]:
    """Return a shallow copy with every failing field replaced by its
    empty value (list → [], str → ""). Successful fields are preserved
    verbatim — partial extraction beats refusing the whole row."""
    cleaned = dict(parsed)
    for field in failing:
        if field in _TEXT_FIELDS_LIST:
            cleaned[field] = []
        elif field in _TEXT_FIELDS_STR:
            cleaned[field] = ""
    return cleaned


def _record_linter_rejection(*, package: str, to_version: str,
                              violations: list, parsed: dict[str, Any]) -> None:
    """Append a row to the shared linter-rejection telemetry. Reuses
    ``app.threads.linter_telemetry`` (the surface the daily briefing
    already aggregates from). Failure-isolated."""
    try:
        from app.threads.linter_telemetry import record_rejection
        # body_text_len is the closest analogue to "how much LLM
        # output was scrubbed" — sum each fragment's length.
        body_len = sum(len(t) for _, t in _iter_text_fragments(parsed))
        record_rejection(
            thread_id=f"capability:{package}:{to_version}",
            violations=violations,
            body_text_len=body_len,
        )
    except Exception:
        logger.debug("ul: linter rejection telemetry failed", exc_info=True)


_LINT_RETRY_SUFFIX = (
    "IMPORTANT — discipline reminder. Treat the changelog as a technical "
    "document. Use third-person, factual language ('The library adds X', "
    "'API Y was removed'). Do NOT use first-person phenomenal claims "
    "('I noticed', 'I felt', 'I am curious', etc.) — these are forbidden "
    "and will be rejected."
)


def _parse_strict_json(text: str) -> Optional[dict[str, Any]]:
    """Tolerate optional ```json fences but require a single JSON object."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        # Strip the first fence line and the trailing one.
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```\s*$", "", s)
        s = s.strip()
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _coerce_str_list(value: Any, *, cap: int = 200) -> tuple[str, ...]:
    """Coerce LLM output to a tuple[str, ...] with per-entry length cap."""
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if not cleaned:
            continue
        out.append(cleaned[:cap])
    return tuple(out)


# ── Public API ───────────────────────────────────────────────────────────


def already_extracted(package: str, to_version: str) -> bool:
    """True iff a Capability row already exists for ``(package, to_version)``."""
    path = _ledger_path(package)
    if not path.exists():
        return False
    needle_to = _normalize_version(to_version)
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = row.get("payload") or {}
                if _normalize_version(str(payload.get("to_version", ""))) == needle_to:
                    return True
        return False
    except OSError:
        return False


def extract_for_package(
    package: str,
    from_version: str,
    to_version: str,
    *,
    metadata_fetcher: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
    releases_fetcher: Optional[Callable[[str, str], list[dict[str, Any]]]] = None,
    changelog_fetcher: Optional[Callable[[Optional[dict], str, str],
                                          Optional[str]]] = None,
    llm_builder: Optional[Callable[[], Any]] = None,
) -> Optional[Capability]:
    """End-to-end extraction for one upgrade.

    Returns the persisted :class:`Capability`, or None if any stage
    declined (master switch OFF, dedup hit, fetch empty, LLM
    unavailable, parse failure).

    All three fetchers are injectable for tests.
    """
    if not _enabled():
        return None
    if already_extracted(package, to_version):
        return None

    # P1#c — monthly LLM-budget gate. Refuse the extraction when the
    # current month's spend would exceed the operator-set cap. The
    # caller sees None — same shape as any other decline — so the
    # rest of the pipeline doesn't change.
    if remaining_month_extraction_budget() < _ESTIMATED_COST_PER_EXTRACTION_USD:
        logger.debug(
            "ul.u1: extraction budget exhausted for the month; "
            "skipping %s %s→%s",
            package, from_version, to_version,
        )
        return None

    md_fn = metadata_fetcher or _fetch_pypi_metadata
    pypi_metadata = md_fn(package)

    releases: list[dict[str, Any]] = []
    if releases_fetcher is not None:
        try:
            releases = list(releases_fetcher(from_version, to_version) or [])
        except Exception:
            releases = []
    elif pypi_metadata:
        ownerrepo = _github_owner_repo(pypi_metadata)
        if ownerrepo:
            owner, repo = ownerrepo
            raw_releases = _fetch_github_releases(owner, repo, limit=30)
            releases = _releases_between(
                raw_releases, from_version=from_version, to_version=to_version,
            )

    # Gap 5 — third content source. CHANGELOG.md (or equivalent) often
    # has richer, semver-headed release notes than either PyPI's
    # description or sparse GitHub release bodies. Adapter is failure-
    # isolated end-to-end — None on any error path.
    changelog_section: Optional[str] = None
    cl_fn = changelog_fetcher or _fetch_changelog_section
    try:
        changelog_section = cl_fn(pypi_metadata, from_version, to_version)
    except Exception:
        logger.debug("ul: changelog adapter raised; ignored", exc_info=True)

    if not pypi_metadata and not releases and not changelog_section:
        logger.debug("ul: no source material for %s %s→%s",
                     package, from_version, to_version)
        return None

    excerpt, source_label = _assemble_excerpt(
        package=package,
        from_version=from_version,
        to_version=to_version,
        pypi_metadata=pypi_metadata,
        github_releases=releases,
        changelog_section=changelog_section,
    )
    if not excerpt.strip():
        return None

    parsed = _extract_with_llm(
        excerpt=excerpt,
        package=package,
        from_version=from_version,
        to_version=to_version,
        llm_builder=llm_builder,
    )
    # P1#c — charge the budget on EVERY LLM call attempt, even on
    # parse failure. The LLM is paid for the call, not for usable
    # output, so a misbehaving model that returns garbage shouldn't
    # be free to spin.
    _record_extraction_attempt(
        package=package, to_version=to_version,
        succeeded=bool(parsed),
    )
    if not parsed:
        return None

    # Gap 3 — phenomenal-language discipline at the producer. Lint each
    # LLM-emitted text fragment; on HARD_FAIL retry the call once with
    # a strengthened system prompt. If still failing, blank only the
    # offending fields (preserve clean siblings) and record telemetry.
    # Capped at one retry to bound cost — at $0.10/call the worst case
    # is $0.20 per linted extraction.
    failing, _ = _lint_extraction(parsed)
    if failing:
        retried = _extract_with_llm(
            excerpt=excerpt,
            package=package,
            from_version=from_version,
            to_version=to_version,
            llm_builder=llm_builder,
            system_prompt_suffix=_LINT_RETRY_SUFFIX,
        )
        _record_extraction_attempt(
            package=package, to_version=to_version,
            succeeded=bool(retried),
        )
        if retried:
            parsed = retried
            failing, retry_violations = _lint_extraction(parsed)
            if failing:
                _record_linter_rejection(
                    package=package, to_version=to_version,
                    violations=retry_violations, parsed=parsed,
                )
                parsed = _blank_failing_fields(parsed, failing)
        else:
            # Retry hit a transient LLM failure — keep the first parse
            # but scrub the offending fields rather than serving a
            # phenomenal claim. Telemetry uses the first-pass parse.
            _, first_violations = _lint_extraction(parsed)
            _record_linter_rejection(
                package=package, to_version=to_version,
                violations=first_violations, parsed=parsed,
            )
            parsed = _blank_failing_fields(parsed, failing)

    excerpt_for_hash = excerpt[:_MAX_EXCERPT_FOR_HASH].encode("utf-8")
    cap = Capability(
        package=package,
        from_version=from_version,
        to_version=to_version,
        source=source_label,
        extracted_at=datetime.now(timezone.utc).isoformat(),
        new_features=_coerce_str_list(parsed.get("new_features")),
        deprecations=_coerce_str_list(parsed.get("deprecations")),
        breaking_changes=_coerce_str_list(parsed.get("breaking_changes")),
        security_fixes=_coerce_str_list(parsed.get("security_fixes")),
        perf_notes=_coerce_str_list(parsed.get("perf_notes")),
        license_change=str(parsed.get("license_change") or "")[:200],
        notes=str(parsed.get("notes") or "")[:1000],
        raw_excerpt_sha256=hashlib.sha256(excerpt_for_hash).hexdigest(),
    )

    _persist(cap)
    return cap


def _persist(cap: Capability) -> None:
    """Append a Capability to its package ledger with a fresh hash link."""
    path = _ledger_path(cap.package)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.debug("ul: persist mkdir failed", exc_info=True)
        return
    payload = cap.to_payload()
    prev_hash = _last_hash_for(cap.package)
    row_hash = _compute_row_hash(prev_hash, payload)
    row = {
        "payload": payload,
        "prev_hash": prev_hash,
        "hash": row_hash,
    }
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(_canonical_json(row) + "\n")
    except OSError:
        logger.debug("ul: persist append failed", exc_info=True)


def read_capabilities(
    package: str,
    *,
    since_iso: Optional[str] = None,
) -> list[Capability]:
    """Read all (or post-``since_iso``) Capability rows for *package*."""
    path = _ledger_path(package)
    if not path.exists():
        return []
    out: list[Capability] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    payload = row.get("payload") or {}
                except json.JSONDecodeError:
                    continue
                if since_iso and str(payload.get("extracted_at", "")) < since_iso:
                    continue
                try:
                    out.append(Capability(
                        package=str(payload.get("package", "")),
                        from_version=str(payload.get("from_version", "")),
                        to_version=str(payload.get("to_version", "")),
                        source=str(payload.get("source", "")),
                        extracted_at=str(payload.get("extracted_at", "")),
                        new_features=tuple(payload.get("new_features") or ()),
                        deprecations=tuple(payload.get("deprecations") or ()),
                        breaking_changes=tuple(payload.get("breaking_changes") or ()),
                        security_fixes=tuple(payload.get("security_fixes") or ()),
                        perf_notes=tuple(payload.get("perf_notes") or ()),
                        license_change=str(payload.get("license_change") or ""),
                        notes=str(payload.get("notes") or ""),
                        raw_excerpt_sha256=str(payload.get("raw_excerpt_sha256") or ""),
                    ))
                except Exception:
                    continue
    except OSError:
        return []
    return out


def verify_chain(package: str) -> tuple[bool, Optional[int]]:
    """Walk the hash chain for *package*. Returns ``(ok, broken_at_row)``.

    ``broken_at_row`` is None on success, 0-indexed row number on
    first mismatch.
    """
    path = _ledger_path(package)
    if not path.exists():
        return True, None
    prev = _GENESIS_HASH
    try:
        with path.open("r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    return False, idx
                payload = row.get("payload") or {}
                recorded_prev = str(row.get("prev_hash") or "")
                recorded_hash = str(row.get("hash") or "")
                if recorded_prev != prev:
                    return False, idx
                expected = _compute_row_hash(prev, payload)
                if expected != recorded_hash:
                    return False, idx
                prev = recorded_hash
    except OSError:
        return False, None
    return True, None


def run_one_batch(
    candidates: list[tuple[str, str, str]],
    *,
    max_per_batch: int = 8,
    metadata_fetcher: Optional[Callable[[str], Optional[dict[str, Any]]]] = None,
    releases_fetcher: Optional[Callable[[str, str, str], list[dict[str, Any]]]] = None,
    llm_builder: Optional[Callable[[], Any]] = None,
) -> dict[str, Any]:
    """Process up to ``max_per_batch`` ``(package, from_version, to_version)`` tuples.

    Used by U6 (annual snapshot) and by the dependency_radar enrichment
    wiring (U4) to pre-extract capability data before filing CRs.

    ``releases_fetcher`` here takes (from_version, to_version) and
    returns a list of release dicts — kept narrow so tests can stub
    a specific package's responses.

    Returns a summary dict for caller logging:
      {extracted: int, skipped_dedup: int, skipped_disabled: int,
       errors: int, capabilities: [...]}
    """
    out: dict[str, Any] = {
        "extracted": 0,
        "skipped_dedup": 0,
        "skipped_disabled": 0,
        "errors": 0,
        "capabilities": [],
    }
    if not _enabled():
        out["skipped_disabled"] = len(candidates)
        return out

    for (package, from_version, to_version) in candidates[:max_per_batch]:
        if already_extracted(package, to_version):
            out["skipped_dedup"] += 1
            continue
        try:
            # Bind releases_fetcher's first arg (package name) by closure
            # so the public per-package signature matches extract_for_package.
            per_pkg_fetcher: Optional[Callable[[str, str], list[dict[str, Any]]]]
            if releases_fetcher is None:
                per_pkg_fetcher = None
            else:
                def _bind(pkg: str = package) -> Callable[[str, str], list[dict[str, Any]]]:
                    def _f(fv: str, tv: str) -> list[dict[str, Any]]:
                        return list(releases_fetcher(pkg, fv, tv) or [])  # type: ignore[misc]
                    return _f
                per_pkg_fetcher = _bind()
            cap = extract_for_package(
                package, from_version, to_version,
                metadata_fetcher=metadata_fetcher,
                releases_fetcher=per_pkg_fetcher,
                llm_builder=llm_builder,
            )
            if cap is not None:
                out["extracted"] += 1
                out["capabilities"].append(cap)
        except Exception:
            out["errors"] += 1
            logger.debug("ul: batch entry failed for %s", package, exc_info=True)
    return out
