#!/usr/bin/env python3
"""
migrate_tech_radar_proposals.py — Retire the dead-end tech_radar proposal
backlog by routing it through the discovered_models pipeline.

Background: tech_radar_crew.py previously auto-created skill-type proposals
for every model discovery but never attached a files/ directory, so
approve_proposal() was a no-op. The dead-end code has been removed (models
now plant stubs in control_plane.discovered_models via tech_radar's new
integration), leaving a backlog of fileless "New model: ..." proposals
still marked 'pending'.

This script drains that backlog:
  1. Finds every pending proposal whose title starts with "New model:" and
     whose files/ dir is empty — the signature of the old tech_radar path.
  2. Batch-asks an LLM to map each title+summary to an OpenRouter slug
     (or null for hallucinated / non-OpenRouter models).
  3. For each resolved slug, calls llm_discovery._store_stub() so the next
     OpenRouter scan cycle can enrich and benchmark the model.
  4. Rejects every proposal in the set, appending a note to proposal.md
     explaining the migration and whether a stub was planted.

Usage (inside the gateway container):
    python scripts/migrate_tech_radar_proposals.py          # apply
    python scripts/migrate_tech_radar_proposals.py --dry    # preview only

Or from the host:
    docker exec crewai-team-gateway-1 python scripts/migrate_tech_radar_proposals.py

Idempotent: already-rejected proposals are skipped; _store_stub uses
ON CONFLICT DO NOTHING so replanting the same slug is a no-op.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROPOSALS_DIR = Path("/app/workspace/proposals")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._:-]*$")


def _find_pending_tech_radar_proposals() -> list[dict]:
    """Return metadata for every pending proposal that looks like a tech_radar
    model discovery (title starts "New model:" and files/ is empty)."""
    out: list[dict] = []
    if not PROPOSALS_DIR.exists():
        return out
    for pdir in sorted(PROPOSALS_DIR.iterdir()):
        if not pdir.is_dir():
            continue
        status_path = pdir / "status.json"
        if not status_path.exists():
            continue
        try:
            status = json.loads(status_path.read_text())
        except Exception:
            continue
        if status.get("status") != "pending":
            continue
        title = str(status.get("title", ""))
        if not title.startswith("New model:"):
            continue
        files_dir = pdir / "files"
        has_files = files_dir.exists() and any(files_dir.rglob("*"))
        if has_files:
            continue  # not the fileless tech_radar shape
        proposal_md = pdir / "proposal.md"
        body = proposal_md.read_text() if proposal_md.exists() else ""
        out.append({
            "id": status.get("id"),
            "dir": pdir,
            "title": title,
            "body": body,
            "status": status,
        })
    return out


def _resolve_slugs(proposals: list[dict]) -> dict[int, str | None]:
    """Ask the LLM for an OpenRouter slug per proposal. Returns {id: slug|None}."""
    from app.llm_factory import create_specialist_llm
    from app.utils import safe_json_parse

    items = []
    for p in proposals:
        items.append({
            "id": p["id"],
            "title": p["title"].replace("New model:", "").strip(),
            "summary": _extract_summary(p["body"]),
        })

    prompt = (
        "You are mapping model names to OpenRouter API slugs.\n\n"
        "For each item below, return the exact OpenRouter slug in the form "
        '"provider/model" (e.g. "moonshotai/kimi-k2-0905", '
        '"anthropic/claude-sonnet-4.5", "z-ai/glm-4.6") IF you are confident '
        "the model is available on OpenRouter right now.\n\n"
        "Return null if the model is not on OpenRouter, was announced but "
        "unreleased, was clearly a hallucinated/unreleased name (e.g. a "
        'future version number like "GPT-5.4 Pro", "Gemini 3.1 Pro"), or '
        "is not an individual model (e.g. a leaderboard or collection).\n\n"
        "Items:\n" + json.dumps(items, indent=2) + "\n\n"
        'Respond with ONLY a JSON object: {"<id>": "provider/slug" | null, ...}'
    )

    llm = create_specialist_llm(max_tokens=2048, role="research")
    raw = str(llm.call(prompt)).strip()
    parsed, _err = safe_json_parse(raw)
    if not isinstance(parsed, dict):
        logger.warning("LLM returned non-dict slug mapping; no slugs will be planted")
        return {p["id"]: None for p in proposals}

    out: dict[int, str | None] = {}
    for p in proposals:
        pid = p["id"]
        val = parsed.get(str(pid)) or parsed.get(pid)
        if isinstance(val, str) and val.strip():
            slug = val.strip().lower()
            if slug.startswith("openrouter/"):
                slug = slug[len("openrouter/"):]
            out[pid] = slug if SLUG_RE.match(slug) else None
        else:
            out[pid] = None
    return out


def _extract_summary(body: str) -> str:
    m = re.search(r"Tech radar discovered:[^\n]*\n\n(.+?)\n\n", body, re.DOTALL)
    if m:
        return m.group(1).strip()[:400]
    return body[:400]


def _plant_stub(slug: str, title: str, proposal_id: int) -> bool:
    from app.llm_discovery import _store_stub
    return _store_stub(
        model_id=f"openrouter/{slug}",
        provider="openrouter",
        display_name=title.replace("New model:", "").strip()[:200],
        source="tech_radar",
        metadata={"migrated_from_proposal": proposal_id},
    )


def _reject_with_note(proposal: dict, stub_planted: str | None) -> None:
    from app.proposals import reject_proposal
    from app.safe_io import safe_write

    note_lines = [
        "",
        "---",
        "## Migration note",
        "",
        f"Closed {datetime.now(timezone.utc).isoformat()} by "
        "`scripts/migrate_tech_radar_proposals.py`.",
        "",
        "Tech-radar model discoveries no longer flow through the proposal "
        "system — they plant stubs in `control_plane.discovered_models` "
        "(`source='tech_radar'`) so the standard benchmark + promotion "
        "pipeline in `app/llm_discovery.py` can pick them up.",
        "",
    ]
    if stub_planted:
        note_lines.append(
            f"Stub planted: `openrouter/{stub_planted}` — awaiting "
            "enrichment by the next OpenRouter discovery cycle."
        )
    else:
        note_lines.append(
            "No stub planted — the LLM could not map this title to a "
            "confirmed OpenRouter slug (likely hallucinated, unreleased, "
            "or not an individual model)."
        )
    note_lines.append("")

    md_path = proposal["dir"] / "proposal.md"
    existing = md_path.read_text() if md_path.exists() else ""
    safe_write(md_path, existing + "\n".join(note_lines))

    reject_proposal(proposal["id"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="Preview without applying")
    args = ap.parse_args()

    proposals = _find_pending_tech_radar_proposals()
    logger.info("Found %d pending tech_radar proposals", len(proposals))
    if not proposals:
        return 0

    for p in proposals:
        logger.info("  #%s — %s", p["id"], p["title"])

    if args.dry:
        logger.info("--dry set: no LLM call, no DB writes, no proposal updates")
        return 0

    slug_map = _resolve_slugs(proposals)
    planted = 0
    rejected = 0
    for p in proposals:
        slug = slug_map.get(p["id"])
        if slug:
            ok = _plant_stub(slug, p["title"], p["id"])
            if ok:
                planted += 1
                logger.info("  #%s → planted stub openrouter/%s", p["id"], slug)
            else:
                logger.warning("  #%s → stub insert FAILED for %s", p["id"], slug)
                slug = None
        else:
            logger.info("  #%s → no slug resolved", p["id"])

        _reject_with_note(p, stub_planted=slug)
        rejected += 1

    logger.info("Done: %d proposals closed, %d stubs planted", rejected, planted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
