"""
wiki_hot_cache.py — Session context file for quick Commander orientation.

Maintains wiki/hot.md (~500 words) with the most relevant recent state:
  - Last 3 tasks processed
  - Active contradictions
  - Stale pages count
  - Recent wiki changes (last 5 log entries)
  - Top-priority knowledge gaps

Updated periodically by idle scheduler. Commander reads alongside index.md.
Inspired by claude-obsidian's session context pattern.
"""

import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def update_hot_cache() -> None:
    """Regenerate wiki/hot.md with current session context."""
    try:
        from app.tools.wiki_tools import WIKI_ROOT, WikiLintTool, _parse_frontmatter, VALID_SECTIONS

        hot_path = os.path.join(WIKI_ROOT, "hot.md")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        sections = []
        sections.append(f"---\ntitle: Hot Cache\nupdated_at: \"{now}\"\n---\n")
        sections.append("# Wiki Hot Cache\n")
        sections.append(f"*Auto-generated {now}. Read this for quick context.*\n")

        # ── Recent wiki changes (last 5 log entries) ─────────────────
        log_path = os.path.join(WIKI_ROOT, "log.md")
        recent_changes = []
        if os.path.isfile(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            # Find table rows (lines starting with |)
            table_lines = [l.strip() for l in lines if l.strip().startswith("|") and "timestamp" not in l.lower() and "---" not in l]
            recent_changes = table_lines[-5:]

        sections.append("## Recent Wiki Changes")
        if recent_changes:
            for line in recent_changes:
                sections.append(f"  {line}")
        else:
            sections.append("  No recent changes.")
        sections.append("")

        # ── Wiki statistics ───────────────────────────────────────────
        total_pages = 0
        section_counts = {}
        stale_count = 0
        contradictions = []

        for sec in VALID_SECTIONS:
            sec_dir = os.path.join(WIKI_ROOT, sec)
            if not os.path.isdir(sec_dir):
                section_counts[sec] = 0
                continue
            count = 0
            for fname in os.listdir(sec_dir):
                if fname == "index.md" or not fname.endswith(".md"):
                    continue
                count += 1
                fpath = os.path.join(sec_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        content = f.read()
                    fm, _ = _parse_frontmatter(content)
                    # Check staleness (90 days)
                    updated = fm.get("updated_at", "")
                    if updated:
                        try:
                            update_dt = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
                            if (datetime.now(timezone.utc) - update_dt).days > 90:
                                stale_count += 1
                        except (ValueError, TypeError):
                            pass
                    # Check contradictions
                    if fm.get("status") == "active":
                        contrad = fm.get("contradicts") or fm.get("contradicted_by")
                        if contrad:
                            contradictions.append(f"{sec}/{fname[:-3]}")
                except Exception:
                    pass
            section_counts[sec] = count
            total_pages += count

        sections.append("## Wiki Stats")
        sections.append(f"  Total pages: {total_pages}")
        for sec, cnt in sorted(section_counts.items()):
            if cnt > 0:
                sections.append(f"  - {sec}: {cnt}")
        sections.append(f"  Stale (>90 days): {stale_count}")
        sections.append("")

        # ── Active contradictions ─────────────────────────────────────
        sections.append("## Active Contradictions")
        if contradictions:
            for c in contradictions[:5]:
                sections.append(f"  - {c}")
        else:
            sections.append("  None.")
        sections.append("")

        # ── Knowledge gaps (sections with 0 pages) ───────────────────
        empty_sections = [s for s, c in section_counts.items() if c == 0]
        sections.append("## Knowledge Gaps")
        if empty_sections:
            for s in empty_sections:
                sections.append(f"  - {s}: no pages yet")
        else:
            sections.append("  All sections populated.")
        sections.append("")

        # Write hot cache
        with open(hot_path, "w", encoding="utf-8") as f:
            f.write("\n".join(sections))

        logger.debug(f"wiki_hot_cache: updated ({total_pages} pages, {stale_count} stale)")
    except Exception:
        logger.debug("wiki_hot_cache: update failed", exc_info=True)
