"""workstream_news — nightly per-workspace news scrape.

Phase B of the morning-briefing workstream section. Once per day, for
each workspace declared under ``workspace/projects/<name>/config.yaml``,
runs a topical web search (the workspace ``description`` field is the
prompt seed) and LLM-clusters the results into 3 short items the
morning briefing can surface.

Output per workspace per day::

    workspace/life_companion/workstream_news/<name>_<YYYY-MM-DD>.json
    {
      "workspace": "plg",
      "display_name": "Protect Group (PLG)",
      "generated_at": "...",
      "items": [
        {"title": "...", "url": "...", "why": "one-line takeaway"},
        ...
      ]
    }

The briefing's ``_gather_workstream_news()`` reads the latest file per
workspace (within a 48h window) and composes the section.

Cost
----
Anthropic Haiku 4.5 over 4 workspaces × ~20 search snippets each ≈
$0.01–0.04 per day. Capped at ``_MAX_WORKSPACES`` × ``_MAX_RESULTS``
to keep the budget bounded.

Safety
------
* Master switch ``LIFE_COMPANION_WORKSTREAM_NEWS_ENABLED`` (default ON).
* All search calls + LLM calls are wrapped in try/except — a broken
  workspace can't stop the others.
* Idempotent within a day: a file already on disk for today is a
  no-op (operator can ``rm`` to force a rerun).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.life_companion._common import (
    audit_event,
    background_enabled,
    feature_enabled,
    state_path,
)

logger = logging.getLogger(__name__)

_STATE_FILE = "workstream_news_state.json"
_OUTPUT_DIR_NAME = "workstream_news"
_MAX_WORKSPACES = 8           # belt-and-suspenders cost ceiling
_MAX_ITEMS_PER_WS = 3         # what the briefing surfaces
_SEARCH_RESULT_CAP = 20       # snippets sent to the LLM per workspace
_MIN_DESCRIPTION_LEN = 12     # ignore workspaces with empty/stub descriptions
def _projects_dir() -> Path:
    """Resolve the workspaces directory. Honors ``WORKSPACE_ROOT`` env
    (set in tests + dev) and falls back to the in-container path."""
    root = os.environ.get("WORKSPACE_ROOT")
    if root:
        return Path(root) / "projects"
    container = Path("/app/workspace/projects")
    if container.exists():
        return container
    # Host-dev fallback — repo-relative.
    return Path(__file__).resolve().parents[2] / "workspace" / "projects"
_MODEL = "claude-haiku-4-5-20251001"


# ── Workspace discovery ───────────────────────────────────────────────


@dataclass(frozen=True)
class _Workspace:
    name: str
    display_name: str
    description: str


def _discover_workspaces() -> list[_Workspace]:
    """Read ``workspace/projects/<name>/config.yaml`` for every project.

    Returns at most ``_MAX_WORKSPACES`` workspaces with non-trivial
    descriptions. Soft-fails when the projects dir is missing.
    """
    out: list[_Workspace] = []
    if not _projects_dir().exists():
        return out
    for child in sorted(_projects_dir().iterdir()):
        if not child.is_dir():
            continue
        cfg = child / "config.yaml"
        if not cfg.exists():
            continue
        try:
            raw = cfg.read_text(encoding="utf-8")
        except OSError:
            continue
        # Tiny key:value YAML parser — these configs are flat and we
        # don't want to add a PyYAML dep just for 3 fields.
        fields: dict[str, str] = {}
        for line in raw.splitlines():
            if ":" not in line or line.lstrip().startswith("#"):
                continue
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
        description = fields.get("description", "")
        if len(description) < _MIN_DESCRIPTION_LEN:
            continue
        out.append(_Workspace(
            name=child.name,
            display_name=fields.get("display_name") or child.name,
            description=description,
        ))
        if len(out) >= _MAX_WORKSPACES:
            break
    return out


# ── Search + LLM ──────────────────────────────────────────────────────


def _build_query(ws: _Workspace) -> str:
    """Compose a web-search query from the workspace description.

    Adds ``"news OR update OR announcement"`` so we bias toward
    recent editorial coverage rather than evergreen marketing
    pages. The description text itself already encodes the topic.
    """
    desc = ws.description.strip().rstrip(".")
    return f'{desc} (news OR update OR announcement) past_week'


def _search(query: str) -> list[dict]:
    """Wrap ``app.tools.web_search.web_search`` and return structured
    results when possible. The current return-type is a human-readable
    string; we keep snippets verbatim and let the LLM cluster.

    Returns a list of ``{title, url, snippet}`` dicts, possibly empty.
    """
    try:
        from app.tools.web_search import web_search
    except Exception:
        return []
    try:
        raw = web_search(query) or ""
    except Exception:
        return []
    # The current ``web_search`` returns markdown-ish blocks. Parse
    # lightly — we only need title + url + snippet for the LLM prompt.
    items: list[dict] = []
    for chunk in re.split(r"\n\s*\n", raw)[:_SEARCH_RESULT_CAP]:
        title = ""
        url = ""
        snippet = ""
        for line in chunk.splitlines():
            m = re.match(r"^\s*(?:#+\s*)?(?:\d+[\).]\s*)?\*?\*?(.+?)\*?\*?$", line)
            if not title and m and len(m.group(1)) > 6:
                title = m.group(1).strip()
                continue
            u = re.search(r"(https?://[^\s)]+)", line)
            if not url and u:
                url = u.group(1)
                continue
            snippet += " " + line.strip()
        snippet = snippet.strip()[:400]
        if title and (url or snippet):
            items.append({"title": title[:200], "url": url, "snippet": snippet})
    return items


_SYSTEM_PROMPT = """\
You are filtering news/updates for a specific workstream the user runs.

You will be given the workstream's name + description, and a set of
web-search snippets. Pick the most relevant {n} items, written so the
user can scan the briefing in 10 seconds.

For each item, return:
  * "title": the headline (≤90 chars)
  * "url":   the source URL if present in the snippet (else empty)
  * "why":   one-line takeaway specific to this workstream (≤120 chars)

DROP items that are not actually about the workstream's topic, even if
the search returned them. DROP marketing / SEO / listicle pages.

Output STRICT JSON exactly:
{"items": [{"title": "...", "url": "...", "why": "..."}, ...]}

If nothing meets the bar, return {"items": []} — empty is better than
filler.
"""


def _classify_with_llm(ws: _Workspace, snippets: list[dict]) -> list[dict]:
    """Send snippets to Anthropic Haiku 4.5; return up to
    ``_MAX_ITEMS_PER_WS`` items. Soft-fail returns ``[]``."""
    if not snippets:
        return []
    try:
        from app.llm_factory import chat_completion_for_role
        client = chat_completion_for_role(role="cheap-vetting")
    except Exception:
        return []
    rendered = "\n".join(
        f"- {s['title']} | {s['url']} | {s['snippet']}" for s in snippets[:_SEARCH_RESULT_CAP]
    )
    user_msg = (
        f"Workstream: {ws.display_name}\n"
        f"Description: {ws.description}\n\n"
        f"Search snippets:\n{rendered}"
    )
    try:
        resp = client.create(
            max_tokens=800,
            system=_SYSTEM_PROMPT.replace("{n}", str(_MAX_ITEMS_PER_WS)),
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception:
        return []
    text = resp.choices[0].message.content or ""
    # Tolerate code-fenced JSON.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    items = parsed.get("items") if isinstance(parsed, dict) else []
    if not isinstance(items, list):
        return []
    cleaned: list[dict] = []
    for it in items[:_MAX_ITEMS_PER_WS]:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "")).strip()[:200]
        url = str(it.get("url", "")).strip()
        why = str(it.get("why", "")).strip()[:300]
        if not title:
            continue
        cleaned.append({"title": title, "url": url, "why": why})
    return cleaned


# ── Persistence + read API ────────────────────────────────────────────


def _output_dir() -> Path:
    p = state_path(_OUTPUT_DIR_NAME)
    # ``state_path`` returns a file path under workspace/life_companion;
    # for a directory we just need the path with the right parent.
    p.mkdir(parents=True, exist_ok=True)
    return p


def _output_path(ws_name: str, day_iso: str) -> Path:
    return _output_dir() / f"{ws_name}_{day_iso}.json"


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _write(ws: _Workspace, items: list[dict], day_iso: str) -> Path:
    path = _output_path(ws.name, day_iso)
    payload = {
        "workspace": ws.name,
        "display_name": ws.display_name,
        "description": ws.description,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": _MODEL,
        "items": items,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)
    return path


def recent_news(workspace_name: str, *, window_days: int = 2) -> list[dict]:
    """Read the latest news file for ``workspace_name`` within
    ``window_days``. Returns ``[]`` when none exists."""
    out_dir = _output_dir()
    if not out_dir.exists():
        return []
    candidates = sorted(out_dir.glob(f"{workspace_name}_*.json"), reverse=True)
    cutoff = time.time() - window_days * 86400
    for p in candidates:
        try:
            if p.stat().st_mtime < cutoff:
                break
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = data.get("items") or []
        if isinstance(items, list):
            return items
    return []


def all_workspaces_recent_news(*, window_days: int = 2) -> dict[str, dict[str, Any]]:
    """Convenience read for the briefing — returns
    ``{workspace_name: {display_name, items}}``."""
    out: dict[str, dict[str, Any]] = {}
    for ws in _discover_workspaces():
        items = recent_news(ws.name, window_days=window_days)
        if items:
            out[ws.name] = {"display_name": ws.display_name, "items": items}
    return out


# ── Idle-job entry point ──────────────────────────────────────────────


def _cadence_seconds() -> float:
    """≥18h between full passes so the operator's morning briefing
    always has fresh news but we never spend more than $0.05/day."""
    return float(os.getenv("LIFE_COMPANION_WORKSTREAM_NEWS_INTERVAL_SECONDS", "64800"))


def run() -> None:
    """Idempotent daily pass. Each workspace's per-day file is written
    at most once; subsequent calls within the same UTC day are no-ops
    for that workspace."""
    if not feature_enabled("workstream_news"):
        return
    if not background_enabled():
        return
    # Lightweight state file — last-pass timestamp.
    state_p = state_path(_STATE_FILE)
    try:
        state = json.loads(state_p.read_text()) if state_p.exists() else {}
    except Exception:
        state = {}
    last = float(state.get("last_run_at", 0.0))
    if time.monotonic() - last < _cadence_seconds() and last > 0:
        return

    day = _today_iso()
    workspaces = _discover_workspaces()
    if not workspaces:
        return

    n_written = 0
    n_skipped = 0
    n_failed = 0
    for ws in workspaces:
        path = _output_path(ws.name, day)
        if path.exists():
            n_skipped += 1
            continue
        try:
            snippets = _search(_build_query(ws))
            items = _classify_with_llm(ws, snippets)
            _write(ws, items, day)
            n_written += 1
        except Exception:
            logger.debug(
                "workstream_news: workspace %s failed", ws.name, exc_info=True
            )
            n_failed += 1
            continue

    state["last_run_at"] = time.monotonic()
    state["last_run_iso"] = datetime.now(timezone.utc).isoformat()
    try:
        tmp = state_p.with_suffix(state_p.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(state_p)
    except Exception:
        pass

    audit_event(
        "workstream_news_pass",
        day=day,
        n_workspaces=len(workspaces),
        n_written=n_written,
        n_skipped=n_skipped,
        n_failed=n_failed,
    )
