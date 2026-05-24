"""Build + render + persist the capability inventory.

Reads four registries:

  * ``app.tool_registry.registry.ToolRegistry.instance()`` — tools.
  * ``app.healing.monitors._DEFAULT_CADENCE_S`` — healing monitors.
  * ``app.companion.loop.get_idle_jobs()`` — idle jobs.
  * ``app.agents.commander.command_registry.SIGNAL_COMMANDS`` — commands.

The render is deterministic — same registries produce same bytes —
so the operator can diff month-over-month to see capability gain/loss
without noise from formatting drift.

Operator pin blocks
-------------------

Hand-written sections inside markdown comments::

    <!-- pin id="operating-notes" -->
    Free-form operator notes. The writer never touches text inside.
    <!-- /pin -->

A new render preserves the pin block at its original position. If the
operator removes the marker, the pin's content is folded into the
auto-generated section on the next pass.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


_PIN_RE = re.compile(
    r'(<!--\s*pin\s+id="(?P<id>[^"]+)"\s*-->\n?(?P<body>.*?)\n?<!--\s*/pin\s*-->)',
    re.DOTALL,
)


def _workspace_root() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT
        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _wiki_root() -> Path:
    """Wiki sits next to (not inside) workspace on the running gateway."""
    ws = _workspace_root()
    # Mirror the existing wiki path resolution used by other writers
    # (annual_reflection, legacy_essay, etc.) — same parent.
    parent = ws.parent
    wiki = parent / "wiki"
    if wiki.exists() or not (ws / "wiki").exists():
        return wiki
    return ws / "wiki"


def _output_path() -> Path:
    return _wiki_root() / "self" / "capability_inventory.md"


# ── Datamodels ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolEntry:
    name: str
    tier: str
    lifecycle: str
    capabilities: tuple[str, ...]
    description: str
    is_loadable: bool


@dataclass(frozen=True)
class MonitorEntry:
    name: str
    cadence_seconds: int


@dataclass(frozen=True)
class IdleJobEntry:
    name: str
    weight: str


@dataclass(frozen=True)
class CommandEntry:
    command: str
    aliases: tuple[str, ...]
    syntax: str
    description: str
    category: str


@dataclass
class Inventory:
    as_of: str
    tools: list[ToolEntry] = field(default_factory=list)
    monitors: list[MonitorEntry] = field(default_factory=list)
    idle_jobs: list[IdleJobEntry] = field(default_factory=list)
    commands: list[CommandEntry] = field(default_factory=list)


# ── Collectors ──────────────────────────────────────────────────────────


def _collect_tools() -> list[ToolEntry]:
    try:
        from app.tool_registry.registry import ToolRegistry
        specs = ToolRegistry.instance().all()
    except Exception:
        logger.debug("capability_inventory: tool_registry unavailable", exc_info=True)
        return []
    entries: list[ToolEntry] = []
    for spec in specs:
        try:
            entries.append(ToolEntry(
                name=spec.name,
                tier=spec.tier.value if hasattr(spec.tier, "value") else str(spec.tier),
                lifecycle=spec.lifecycle.value if hasattr(spec.lifecycle, "value") else str(spec.lifecycle),
                capabilities=tuple(spec.capabilities),
                description=(spec.description or "").splitlines()[0] if spec.description else "",
                is_loadable=bool(spec.is_loadable),
            ))
        except Exception:
            logger.debug("capability_inventory: tool entry skipped", exc_info=True)
    entries.sort(key=lambda e: e.name)
    return entries


def _collect_monitors() -> list[MonitorEntry]:
    try:
        from app.healing.monitors import _DEFAULT_CADENCE_S
    except Exception:
        return []
    out = [
        MonitorEntry(name=name, cadence_seconds=int(c))
        for name, c in _DEFAULT_CADENCE_S.items()
    ]
    out.sort(key=lambda e: e.name)
    return out


def _collect_idle_jobs() -> list[IdleJobEntry]:
    try:
        from app.companion.loop import get_idle_jobs
        from app.idle_scheduler import JobWeight
    except Exception:
        return []
    try:
        raw = get_idle_jobs()
    except Exception:
        logger.debug("capability_inventory: get_idle_jobs raised", exc_info=True)
        return []
    out: list[IdleJobEntry] = []
    for entry in raw:
        # Shape: (name, fn) or (name, fn, weight) — tolerate both.
        name = entry[0] if len(entry) >= 1 else "<unknown>"
        weight = entry[2] if len(entry) >= 3 else JobWeight.MEDIUM
        out.append(IdleJobEntry(name=str(name), weight=str(weight)))
    out.sort(key=lambda e: (e.weight, e.name))
    return out


def _collect_commands() -> list[CommandEntry]:
    try:
        from app.agents.commander.command_registry import SIGNAL_COMMANDS
    except Exception:
        return []
    out = [
        CommandEntry(
            command=c.command,
            aliases=tuple(c.aliases),
            syntax=c.syntax,
            description=c.description,
            category=c.category,
        )
        for c in SIGNAL_COMMANDS
    ]
    out.sort(key=lambda e: (e.category, e.command))
    return out


def build_inventory(now: Optional[float] = None) -> Inventory:
    """Capture a snapshot of all four registries. Pure-read; safe to
    call at any time."""
    cur = float(now) if now is not None else time.time()
    iso = datetime.fromtimestamp(cur, tz=timezone.utc).isoformat()
    return Inventory(
        as_of=iso,
        tools=_collect_tools(),
        monitors=_collect_monitors(),
        idle_jobs=_collect_idle_jobs(),
        commands=_collect_commands(),
    )


# ── Render ──────────────────────────────────────────────────────────────


def _fmt_cadence(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _group_by_category(commands: list[CommandEntry]) -> dict[str, list[CommandEntry]]:
    groups: dict[str, list[CommandEntry]] = {}
    for c in commands:
        groups.setdefault(c.category, []).append(c)
    return groups


def _group_by_weight(jobs: list[IdleJobEntry]) -> dict[str, list[IdleJobEntry]]:
    groups: dict[str, list[IdleJobEntry]] = {}
    for j in jobs:
        groups.setdefault(j.weight, []).append(j)
    return groups


def render_markdown(inv: Inventory, *, prior_pins: dict[str, str] | None = None) -> str:
    """Render the inventory to deterministic markdown.

    ``prior_pins`` is the map of pin_id → body extracted from the
    previous output; the new render embeds them at canonical positions
    so the operator's hand-written notes survive every regeneration.
    """
    prior_pins = prior_pins or {}
    lines: list[str] = []
    lines.append("# AndrusAI capability inventory")
    lines.append("")
    lines.append(
        f"_Auto-generated. As of {inv.as_of}. "
        f"Counts: {len(inv.tools)} tools · {len(inv.monitors)} monitors · "
        f"{len(inv.idle_jobs)} idle jobs · {len(inv.commands)} commands._"
    )
    lines.append("")
    lines.append(
        "> Pinned operator notes live inside `<!-- pin id=\"…\" -->` … "
        "`<!-- /pin -->` blocks and survive regeneration."
    )
    lines.append("")

    # Operator-pinned overview at the top.
    overview = prior_pins.get("overview", "")
    lines.append("## Overview")
    lines.append("")
    lines.append('<!-- pin id="overview" -->')
    lines.append(overview or "Add a one-paragraph operator-authored overview here.")
    lines.append("<!-- /pin -->")
    lines.append("")

    # ── Tools ────────────────────────────────────────────────────────
    lines.append("## Tools")
    lines.append("")
    lines.append(
        "Registered via `@register_tool` and discoverable from the "
        "ToolRegistry. The capabilities list is the source of truth for "
        "what each tool advertises to the LLM."
    )
    lines.append("")
    if not inv.tools:
        lines.append("_Tool registry empty or unreachable._")
    else:
        lines.append("| Name | Tier | Lifecycle | Capabilities | Loadable |")
        lines.append("|------|------|-----------|--------------|----------|")
        for t in inv.tools:
            caps = ", ".join(t.capabilities) if t.capabilities else "—"
            lines.append(
                f"| `{t.name}` | {t.tier} | {t.lifecycle} | {caps} | "
                f"{'✓' if t.is_loadable else '✗'} |"
            )
    lines.append("")

    # ── Healing monitors ────────────────────────────────────────────
    lines.append("## Healing monitors")
    lines.append("")
    lines.append(
        "Daemon-thread observers run by `app.healing.monitors`. Each "
        "monitor has an external probe cadence; many also gate "
        "internally on a slower work cadence (weekly / monthly)."
    )
    lines.append("")
    if not inv.monitors:
        lines.append("_Monitor registry unreachable._")
    else:
        lines.append("| Name | Probe cadence |")
        lines.append("|------|---------------|")
        for m in inv.monitors:
            lines.append(f"| `{m.name}` | {_fmt_cadence(m.cadence_seconds)} |")
    lines.append("")

    # ── Idle jobs ────────────────────────────────────────────────────
    lines.append("## Idle jobs")
    lines.append("")
    lines.append(
        "Background work the companion loop runs when the gateway is "
        "idle. LIGHT (~<30s, observability/reconciler) runs in parallel; "
        "MEDIUM and HEAVY are serialized + pausable by the total-cost brake."
    )
    lines.append("")
    if not inv.idle_jobs:
        lines.append("_Idle-job registry unreachable._")
    else:
        by_weight = _group_by_weight(inv.idle_jobs)
        for weight in ("light", "medium", "heavy"):
            entries = by_weight.get(weight, [])
            if not entries:
                continue
            lines.append(f"### {weight.upper()}")
            lines.append("")
            for j in entries:
                lines.append(f"- `{j.name}`")
            lines.append("")
    lines.append("")

    # ── Signal commands ─────────────────────────────────────────────
    lines.append("## Signal commands")
    lines.append("")
    lines.append(
        "Slash-commands the operator can invoke from Signal (or the React "
        "/cp/chat surface, which shares the same dispatch path)."
    )
    lines.append("")
    if not inv.commands:
        lines.append("_Command registry unreachable._")
    else:
        groups = _group_by_category(inv.commands)
        for category in sorted(groups):
            lines.append(f"### {category}")
            lines.append("")
            for c in groups[category]:
                alias_suffix = f" (also: {', '.join(c.aliases)})" if c.aliases else ""
                lines.append(f"- `{c.command}`{alias_suffix} — {c.description}")
                if c.syntax != c.command:
                    lines.append(f"    - syntax: `{c.syntax}`")
            lines.append("")
    lines.append("")

    # Operator-pinned bottom section — appendix for any handwritten material.
    notes = prior_pins.get("notes", "")
    lines.append("## Operator notes")
    lines.append("")
    lines.append('<!-- pin id="notes" -->')
    lines.append(notes or "Free-form operator-authored notes. Survives regeneration.")
    lines.append("<!-- /pin -->")
    lines.append("")

    return "\n".join(lines)


def _extract_pins(text: str) -> dict[str, str]:
    """Return ``{id: body}`` for every pinned block in ``text``."""
    out: dict[str, str] = {}
    for m in _PIN_RE.finditer(text or ""):
        pin_id = m.group("id")
        body = m.group("body").strip()
        out[pin_id] = body
    return out


def write_inventory(inv: Optional[Inventory] = None) -> Path:
    """Build (or accept) an inventory and write it to disk, preserving
    any operator pins from the previous file. Returns the output path.
    """
    inv = inv if inv is not None else build_inventory()
    out = _output_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    prior_pins: dict[str, str] = {}
    if out.exists():
        try:
            prior_pins = _extract_pins(out.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("capability_inventory: prior pins unreadable", exc_info=True)
    body = render_markdown(inv, prior_pins=prior_pins)
    out.write_text(body, encoding="utf-8")
    return out


def run_once() -> dict[str, Any]:
    """Idle-job entry point. Returns a small summary dict."""
    try:
        from app.runtime_settings import get_capability_inventory_enabled
        if not get_capability_inventory_enabled():
            return {"ran": False, "skipped": True}
    except Exception:
        pass
    try:
        inv = build_inventory()
        path = write_inventory(inv)
        return {
            "ran": True,
            "path": str(path),
            "as_of": inv.as_of,
            "counts": {
                "tools": len(inv.tools),
                "monitors": len(inv.monitors),
                "idle_jobs": len(inv.idle_jobs),
                "commands": len(inv.commands),
            },
        }
    except Exception as exc:
        logger.warning("capability_inventory: run_once failed", exc_info=True)
        return {"ran": True, "error": str(exc)}
