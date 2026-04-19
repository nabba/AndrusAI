"""Obsidian-style notes viewer API.

Serves markdown files from configurable root directories along with parsed
frontmatter, outgoing links, backlinks, a search index, and a graph view.

All routes are prefixed with /api/cp/notes/.

Root directories are configurable via the NOTES_ROOTS env var (JSON object
mapping a short name to an absolute directory). If unset, a few sensible
BotArmy defaults are probed and the ones that exist are exposed.

Path safety: every relative path is resolved against its root and rejected
if the resolved path escapes the root (symlink or ``..`` traversal).
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cp/notes", tags=["notes"])

# ── Roots ───────────────────────────────────────────────────────────────────

_DEFAULT_ROOT_CANDIDATES: list[tuple[str, str]] = [
    # Wiki: container path first, host path as fallback. Whichever exists wins.
    ("wiki", "/app/wiki"),
    ("wiki", "/Users/andrus/BotArmy/crewai-team/wiki"),
    ("docs", "/app/docs"),
    ("docs", "/Users/andrus/BotArmy/crewai-team/docs"),
    ("memory", "/Users/andrus/.claude/projects/-Users-andrus-BotArmy/memory"),
    ("workspace", "/app/workspace"),
    ("workspace", "/Users/andrus/BotArmy/crewai-team/workspace"),
    ("botarmy", "/Users/andrus/BotArmy"),
]


def _load_roots() -> dict[str, Path]:
    """Return {name: absolute_dir}. Order: NOTES_ROOTS env var, then defaults."""
    raw = os.environ.get("NOTES_ROOTS")
    if raw:
        try:
            parsed = json.loads(raw)
            return {
                name: Path(path).resolve()
                for name, path in parsed.items()
                if Path(path).is_dir()
            }
        except Exception as exc:
            logger.warning("notes_api: NOTES_ROOTS parse failed: %s", exc)

    roots: dict[str, Path] = {}
    for name, raw_path in _DEFAULT_ROOT_CANDIDATES:
        p = Path(raw_path)
        if p.is_dir():
            roots[name] = p.resolve()
    return roots


_ROOTS = _load_roots()

# Skip these directory names entirely when walking.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "build", "dist",
    ".vscode", ".idea", ".DS_Store", ".next", "coverage",
    # BotArmy-specific noise
    "deploy_backups", "serve-root", "applied_code",
}
_NOTE_EXTS = {".md", ".mdx", ".markdown"}
_ATTACHMENT_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
    ".pdf",
}

# ── Regexes ─────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# [[Target|Alias]] or [[Target#Heading|Alias]] or [[Target^block|Alias]]
_WIKILINK_RE = re.compile(r"\[\[([^\]\r\n|#^]+?)(?:[#^][^\]|]*)?(?:\|([^\]]+))?\]\]")
# Standard markdown link [text](url)
_MDLINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# #tag — word-like, not at the start of a hash-rule heading or mid-word
_TAG_RE = re.compile(r"(?:^|(?<=\s))#([A-Za-z][A-Za-z0-9_\-/]*)")

# ── Simple TTL cache for the note index ─────────────────────────────────────

_INDEX_TTL_SECONDS = 30.0
_index_cache: dict[str, tuple[float, dict]] = {}


def _now() -> float:
    return time.monotonic()


# ── Utilities ───────────────────────────────────────────────────────────────

def _safe_resolve(root: str, rel_path: str) -> Path:
    base = _ROOTS.get(root)
    if not base:
        raise HTTPException(404, f"Unknown root: {root}")
    if not rel_path:
        return base
    target = (base / rel_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(403, "Path traversal denied")
    return target


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Tiny YAML-ish frontmatter parser — no external deps.

    Supports: ``key: value``, ``key: [a, b, c]``, and YAML list form
    (``key:`` followed by ``  - item`` lines). Quoted strings are unquoted.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    fm: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    def _finish_list() -> None:
        nonlocal current_key, current_list
        if current_list is not None and current_key is not None:
            fm[current_key] = current_list
        current_key = None
        current_list = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # list item continuation
        if current_list is not None and stripped.startswith("-"):
            current_list.append(stripped[1:].strip().strip("\"'"))
            continue
        # not a continuation — flush any pending list
        if current_list is not None:
            _finish_list()
        if ":" not in stripped:
            continue
        k, _, v = stripped.partition(":")
        k = k.strip()
        v = v.strip()
        if not v:
            current_key = k
            current_list = []
            continue
        if v.startswith("[") and v.endswith("]"):
            items = [s.strip().strip("\"'") for s in v[1:-1].split(",") if s.strip()]
            fm[k] = items
        else:
            fm[k] = v.strip("\"'")
    _finish_list()
    return fm, body


def _extract_title(rel_path: str, frontmatter: dict[str, Any], body: str) -> str:
    fm_title = frontmatter.get("title")
    if isinstance(fm_title, str) and fm_title.strip():
        return fm_title.strip()
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    # Fall back to the filename without the extension.
    return Path(rel_path).stem or rel_path


def _walk_notes(root: str) -> list[dict[str, Any]]:
    """Return [{rel_path, size, mtime}, ...] for every markdown note under a root."""
    base = _ROOTS[root]
    out: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix.lower() not in _NOTE_EXTS:
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            rel = full.relative_to(base).as_posix()
            out.append({"rel_path": rel, "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda r: r["rel_path"])
    return out


def _resolve_wikilink(target: str, from_rel: str, notes_by_slug: dict[str, str]) -> str | None:
    """Resolve ``[[Target]]`` to a rel_path inside the same root.

    Strategy: case-insensitive match on either full relative path or bare
    filename. Slashes/backslashes in the target are honored for explicit
    folder refs (``[[sub/Note]]``).
    """
    t = target.strip()
    if not t:
        return None
    key = t.lower()
    # Exact relative path
    hit = notes_by_slug.get(key)
    if hit:
        return hit
    # Try with .md suffix
    hit = notes_by_slug.get(key + ".md")
    if hit:
        return hit
    # Try bare filename via indexed stem table
    return notes_by_slug.get(Path(key).name)


def _build_index(root: str) -> dict[str, Any]:
    """Scan every note and build the backlink/graph/search index.

    Cached with a 30 s TTL. The cache key is the root name; the cached
    payload contains everything endpoints need.
    """
    now = _now()
    cached = _index_cache.get(root)
    if cached and now - cached[0] < _INDEX_TTL_SECONDS:
        return cached[1]

    files = _walk_notes(root)

    # Slug tables used to resolve wikilinks.
    by_slug: dict[str, str] = {}
    for f in files:
        rel = f["rel_path"]
        by_slug[rel.lower()] = rel
        by_slug[Path(rel).stem.lower()] = rel  # bare name
        by_slug[Path(rel).name.lower()] = rel  # with extension

    notes: dict[str, dict[str, Any]] = {}
    outgoing: dict[str, set[str]] = {}  # from -> {to}
    incoming: dict[str, set[str]] = {}
    tags: dict[str, set[str]] = {}  # tag -> {rel_paths}

    for f in files:
        rel = f["rel_path"]
        try:
            text = (_ROOTS[root] / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, body = _parse_frontmatter(text)
        title = _extract_title(rel, fm, body)
        note_tags: set[str] = set()
        # Frontmatter tags (list or CSV string).
        fm_tags = fm.get("tags")
        if isinstance(fm_tags, list):
            for t in fm_tags:
                if isinstance(t, str) and t:
                    note_tags.add(t.lstrip("#"))
        elif isinstance(fm_tags, str):
            for t in re.split(r"[,\s]+", fm_tags):
                t = t.strip().lstrip("#")
                if t:
                    note_tags.add(t)
        # Body tags
        for m in _TAG_RE.finditer(body):
            note_tags.add(m.group(1))

        out_links: set[str] = set()
        # Wikilinks
        for m in _WIKILINK_RE.finditer(body):
            tgt = _resolve_wikilink(m.group(1), rel, by_slug)
            if tgt and tgt != rel:
                out_links.add(tgt)
        # Markdown links to local .md files
        for m in _MDLINK_RE.finditer(body):
            url = m.group(2)
            if url.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not url.endswith(tuple(_NOTE_EXTS)):
                continue
            tgt = _resolve_wikilink(url.split("#", 1)[0], rel, by_slug)
            if tgt and tgt != rel:
                out_links.add(tgt)

        notes[rel] = {
            "rel_path": rel,
            "title": title,
            "size": f["size"],
            "mtime": f["mtime"],
            "tags": sorted(note_tags),
        }
        outgoing[rel] = out_links
        for t in note_tags:
            tags.setdefault(t, set()).add(rel)

    # Invert to build backlinks.
    for src, tgts in outgoing.items():
        for tgt in tgts:
            incoming.setdefault(tgt, set()).add(src)

    payload = {
        "root": root,
        "root_dir": str(_ROOTS[root]),
        "notes": notes,
        "outgoing": {k: sorted(v) for k, v in outgoing.items()},
        "incoming": {k: sorted(v) for k, v in incoming.items()},
        "tags": {t: sorted(paths) for t, paths in tags.items()},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _index_cache[root] = (now, payload)
    return payload


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/roots")
def list_roots():
    """Return configured roots and their absolute paths."""
    return {
        "roots": [
            {"name": name, "path": str(path)}
            for name, path in _ROOTS.items()
        ],
        "default_root": next(iter(_ROOTS), None),
    }


@router.get("/tree")
def get_tree(root: str = Query(...)):
    """Return a folder tree (directories + note files) under a root."""
    base = _ROOTS.get(root)
    if not base:
        raise HTTPException(404, f"Unknown root: {root}")

    def _node(path: Path) -> dict[str, Any]:
        rel = "" if path == base else path.relative_to(base).as_posix()
        node: dict[str, Any] = {
            "name": path.name or root,
            "path": rel,
            "type": "dir",
            "children": [],
        }
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return node
        for entry in entries:
            if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
                continue
            if entry.is_dir():
                child = _node(entry)
                if child["children"]:
                    node["children"].append(child)
            elif entry.is_file():
                suffix = entry.suffix.lower()
                if suffix not in _NOTE_EXTS and suffix not in _ATTACHMENT_EXTS:
                    continue
                node["children"].append({
                    "name": entry.name,
                    "path": entry.relative_to(base).as_posix(),
                    "type": "note" if suffix in _NOTE_EXTS else "attachment",
                    "size": entry.stat().st_size,
                })
        return node

    return {"root": root, "tree": _node(base)}


@router.get("/file")
def get_file(root: str = Query(...), path: str = Query(...)):
    """Return parsed note payload: frontmatter, body, outgoing + incoming links, backlinks."""
    target = _safe_resolve(root, path)
    if not target.is_file():
        raise HTTPException(404, "Note not found")
    if target.suffix.lower() not in _NOTE_EXTS:
        raise HTTPException(400, "Not a markdown note")

    text = target.read_text(encoding="utf-8", errors="replace")
    fm, body = _parse_frontmatter(text)
    title = _extract_title(path, fm, body)
    index = _build_index(root)

    incoming = index["incoming"].get(path, [])
    outgoing = index["outgoing"].get(path, [])

    notes_meta = index["notes"]
    backlinks = [
        {
            "path": p,
            "title": notes_meta.get(p, {}).get("title", p),
        }
        for p in incoming
    ]
    forwardlinks = [
        {
            "path": p,
            "title": notes_meta.get(p, {}).get("title", p),
        }
        for p in outgoing
    ]

    return {
        "root": root,
        "path": path,
        "title": title,
        "frontmatter": fm,
        "body": body,
        "size": target.stat().st_size,
        "mtime": target.stat().st_mtime,
        "backlinks": backlinks,
        "forward_links": forwardlinks,
        "tags": notes_meta.get(path, {}).get("tags", []),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/attachment")
def get_attachment(root: str = Query(...), path: str = Query(...)):
    """Serve a non-markdown file (images, PDFs) directly."""
    target = _safe_resolve(root, path)
    if not target.is_file():
        raise HTTPException(404, "Attachment not found")
    if target.suffix.lower() not in _ATTACHMENT_EXTS:
        raise HTTPException(400, "File type not permitted")
    mime, _ = mimetypes.guess_type(target.name)
    return FileResponse(str(target), media_type=mime or "application/octet-stream")


@router.get("/graph")
def get_graph(root: str = Query(...)):
    """Return graph data (nodes + edges) for the force-directed view."""
    index = _build_index(root)
    nodes = []
    for rel, meta in index["notes"].items():
        folder = str(Path(rel).parent).replace(".", "") or "_root"
        nodes.append({
            "id": rel,
            "label": meta["title"],
            "group": folder,
            "size": meta["size"],
            "tags": meta["tags"],
        })
    edges = []
    for src, tgts in index["outgoing"].items():
        for tgt in tgts:
            edges.append({"source": src, "target": tgt})
    return {
        "root": root,
        "nodes": nodes,
        "edges": edges,
        "tags": sorted(index["tags"].keys()),
        "updated_at": index["updated_at"],
    }


@router.get("/search")
def search_notes(
    root: str = Query(...),
    q: str = Query(..., min_length=1),
    limit: int = Query(50, ge=1, le=200),
):
    """Case-insensitive substring search across title, body, and frontmatter."""
    base = _ROOTS.get(root)
    if not base:
        raise HTTPException(404, f"Unknown root: {root}")
    needle = q.lower()
    index = _build_index(root)
    hits: list[dict[str, Any]] = []
    for rel, meta in index["notes"].items():
        if len(hits) >= limit:
            break
        full = base / rel
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        if meta["title"].lower().find(needle) < 0 and low.find(needle) < 0:
            continue
        # Build a short snippet around the first match (or the title).
        idx = low.find(needle)
        if idx >= 0:
            start = max(0, idx - 60)
            end = min(len(text), idx + len(needle) + 120)
            snippet = text[start:end].replace("\n", " ")
            if start > 0:
                snippet = "…" + snippet
            if end < len(text):
                snippet = snippet + "…"
        else:
            snippet = meta["title"]
        hits.append({
            "path": rel,
            "title": meta["title"],
            "snippet": snippet,
            "tags": meta["tags"],
        })
    return {"query": q, "hits": hits, "total": len(hits)}


@router.get("/tags")
def list_tags(root: str = Query(...)):
    """Return tag counts and paths for every tag."""
    index = _build_index(root)
    return {
        "root": root,
        "tags": [
            {"tag": t, "count": len(paths), "paths": paths}
            for t, paths in sorted(index["tags"].items(), key=lambda x: (-len(x[1]), x[0]))
        ],
    }
