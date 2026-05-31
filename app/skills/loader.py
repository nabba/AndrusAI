"""SKILL.md loader — define a reusable skill in a markdown file and register it.

A skill file is YAML-ish front-matter (a ``---`` fence) followed by a markdown
body that IS the ``task_template`` (``{placeholder}`` substitution comes for
free from ``registry.save_skill``). Example::

    ---
    name: literature-review
    description: Survey recent literature on a topic and summarise it
    force_tier: mid
    task_hint: research
    extra_tools:
      - web_search
      - pdf_compose
    ---
    Survey recent literature on {topic}. Search arXiv and our KB, then
    write a {length}-paragraph synthesis with citations.

This is the file-based counterpart to the ``/skill save`` Signal command:
checking a SKILL.md into the repo (or dropping one in ``skills/``) registers a
skill on load, so workflows live in version control rather than only in the
runtime JSON store.

The front-matter parser is a deliberately tiny stdlib reader (scalars, inline
``[a, b]`` lists, and block ``-`` lists) — no PyYAML dependency, matching the
stdlib-YAML pattern already used by ``upgrade_lifecycle.apply_hook``. Parsing
is pure and side-effect free; persistence goes through the injectable
``save_fn`` so callers (and tests) control where skills land.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_FENCE = "---"
_KEY_VALUE = re.compile(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$")
_BLOCK_ITEM = re.compile(r"^\s*-\s+(.*)$")

# Front-matter keys we recognise; anything else is ignored.
_SCALAR_KEYS = ("name", "description", "force_tier", "task_hint")
_LIST_KEYS = ("extra_tools",)


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Split ``text`` into ``(metadata, body)``.

    Returns ``({}, text)`` when there is no leading ``---`` fence. The body is
    everything after the closing fence, with surrounding blank lines trimmed.
    """
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != _FENCE:
        return {}, (text or "").strip()

    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FENCE:
            close = i
            break
    if close is None:
        # Unterminated fence — treat the whole thing as body, no metadata.
        return {}, (text or "").strip()

    meta: dict = {}
    list_key: Optional[str] = None
    for line in lines[1:close]:
        if not line.strip():
            continue
        item = _BLOCK_ITEM.match(line)
        if item and list_key:
            meta[list_key].append(_unquote(item.group(1)))
            continue
        kv = _KEY_VALUE.match(line)
        if not kv:
            continue
        key, raw = kv.group(1), kv.group(2).strip()
        if raw == "":
            meta[key] = []          # begins a block list
            list_key = key
        elif raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            meta[key] = [_unquote(x) for x in inner.split(",") if x.strip()] if inner else []
            list_key = None
        else:
            meta[key] = _unquote(raw)
            list_key = None

    body = "\n".join(lines[close + 1:]).strip()
    return meta, body


def parse_skill_md(text: str, *, default_name: Optional[str] = None) -> dict:
    """Parse a SKILL.md document into kwargs for ``save_skill``.

    Returns ``{name, task_template, description, force_tier, extra_tools,
    task_hint}``. Raises ``ValueError`` if the body (task template) is empty or
    no name can be determined.
    """
    meta, body = parse_front_matter(text)
    if not body:
        raise ValueError("SKILL.md has an empty task-template body")

    name = str(meta.get("name") or default_name or "").strip()
    if not name:
        raise ValueError("SKILL.md needs a 'name' in front-matter or a filename to derive it from")

    raw_tools = meta.get("extra_tools")
    if isinstance(raw_tools, list):
        extra_tools = [str(t).strip() for t in raw_tools if str(t).strip()]
    elif isinstance(raw_tools, str) and raw_tools.strip():
        extra_tools = [raw_tools.strip()]
    else:
        extra_tools = []

    force_tier = meta.get("force_tier")
    force_tier = str(force_tier).strip() if force_tier else None

    return {
        "name": name,
        "task_template": body,
        "description": str(meta.get("description") or "").strip(),
        "force_tier": force_tier,
        "extra_tools": extra_tools,
        "task_hint": str(meta.get("task_hint") or "").strip(),
    }


def _default_save():
    from app.skills.registry import save_skill

    return save_skill


def load_skill_md(path, *, save_fn: Optional[Callable] = None):
    """Load one SKILL.md file and persist it. Returns the saved ``Skill``.

    ``save_fn`` defaults to ``registry.save_skill`` (resolved lazily) and is
    injectable for tests. The skill name falls back to the filename stem when
    front-matter omits ``name``.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    kwargs = parse_skill_md(text, default_name=p.stem)
    save = save_fn or _default_save()
    return save(
        kwargs["name"],
        kwargs["task_template"],
        description=kwargs["description"],
        force_tier=kwargs["force_tier"],
        extra_tools=kwargs["extra_tools"],
        task_hint=kwargs["task_hint"],
    )


def load_skills_dir(directory, *, save_fn: Optional[Callable] = None) -> list:
    """Load every front-matter-bearing ``*.md`` under ``directory``.

    Files without a ``---`` front-matter fence are skipped (so a stray
    ``README.md`` is never mistaken for a skill). A malformed skill file is
    logged and skipped — one bad file never aborts the batch. Returns the list
    of successfully-saved skills, sorted by filename.
    """
    d = Path(directory)
    if not d.is_dir():
        return []
    save = save_fn or _default_save()
    loaded = []
    for p in sorted(d.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
            meta, _ = parse_front_matter(text)
            if not meta:
                logger.debug("skills.loader: %s has no front-matter — skipping", p.name)
                continue
            kwargs = parse_skill_md(text, default_name=p.stem)
            loaded.append(
                save(
                    kwargs["name"],
                    kwargs["task_template"],
                    description=kwargs["description"],
                    force_tier=kwargs["force_tier"],
                    extra_tools=kwargs["extra_tools"],
                    task_hint=kwargs["task_hint"],
                )
            )
        except Exception:
            logger.warning("skills.loader: failed to load %s — skipping", p, exc_info=True)
    return loaded
