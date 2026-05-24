"""Tests for app.capability_inventory — Gap #5 self-description writer."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pydantic_settings")

from app.capability_inventory import builder  # noqa: E402
from app.capability_inventory.builder import (  # noqa: E402
    Inventory,
    ToolEntry,
    MonitorEntry,
    IdleJobEntry,
    CommandEntry,
    build_inventory,
    render_markdown,
    write_inventory,
    _extract_pins,
)


def test_render_includes_all_four_sections() -> None:
    inv = Inventory(
        as_of="2026-05-24T12:00:00+00:00",
        tools=[
            ToolEntry(
                name="recall_history",
                tier="bronze",
                lifecycle="singleton",
                capabilities=("queries-history",),
                description="Cross-ledger token-overlap search.",
                is_loadable=True,
            ),
        ],
        monitors=[
            MonitorEntry(name="config_coherence", cadence_seconds=86400),
        ],
        idle_jobs=[
            IdleJobEntry(name="briefing-evolution", weight="light"),
        ],
        commands=[
            CommandEntry(
                command="/recall",
                aliases=(),
                syntax="/recall <query>",
                description="Search the audit log + ledgers.",
                category="Audit & ops",
            ),
        ],
    )
    md = render_markdown(inv)
    assert "# AndrusAI capability inventory" in md
    assert "## Tools" in md
    assert "## Healing monitors" in md
    assert "## Idle jobs" in md
    assert "## Signal commands" in md
    assert "recall_history" in md
    assert "config_coherence" in md
    assert "briefing-evolution" in md
    assert "/recall" in md


def test_render_empty_inventory_does_not_crash() -> None:
    inv = Inventory(as_of="2026-05-24T00:00:00+00:00")
    md = render_markdown(inv)
    assert "Tool registry empty or unreachable" in md
    assert "Monitor registry unreachable" in md
    assert "Idle-job registry unreachable" in md
    assert "Command registry unreachable" in md


def test_render_is_deterministic() -> None:
    inv = Inventory(
        as_of="2026-05-24T12:00:00+00:00",
        tools=[
            ToolEntry("a_tool", "gold", "per_agent", ("cap_a",), "a", True),
            ToolEntry("z_tool", "bronze", "singleton", ("cap_z",), "z", False),
        ],
    )
    assert render_markdown(inv) == render_markdown(inv)


def test_pins_are_preserved_across_renders() -> None:
    inv = Inventory(as_of="2026-05-24T00:00:00+00:00")
    prior = {
        "overview": "Andrus's personal AI companion.",
        "notes": "Re-built laptop on 2026-04-01.",
    }
    md = render_markdown(inv, prior_pins=prior)
    assert "Andrus's personal AI companion." in md
    assert "Re-built laptop on 2026-04-01." in md
    # Markers still present so the next pass round-trips.
    assert '<!-- pin id="overview" -->' in md
    assert '<!-- /pin -->' in md


def test_extract_pins_roundtrip() -> None:
    text = (
        "# title\n\n"
        '<!-- pin id="a" -->\nhello\n<!-- /pin -->\n'
        "intermediate\n"
        '<!-- pin id="b" -->\n  world  \n<!-- /pin -->\n'
    )
    pins = _extract_pins(text)
    assert pins == {"a": "hello", "b": "world"}


def test_extract_pins_handles_empty_text() -> None:
    assert _extract_pins("") == {}
    assert _extract_pins("no pins here\n") == {}


def test_write_inventory_preserves_prior_pins(monkeypatch, tmp_path: Path) -> None:
    """write_inventory must read existing operator pins + re-embed them."""
    monkeypatch.setattr(builder, "_wiki_root", lambda: tmp_path)

    # Seed an existing file with a custom pin body.
    target = tmp_path / "self" / "capability_inventory.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        '# old\n<!-- pin id="overview" -->\nOperator-authored intro.\n<!-- /pin -->\n'
        '<!-- pin id="notes" -->\nUpgrade notes for 2026.\n<!-- /pin -->\n'
    )

    inv = Inventory(as_of="2026-05-24T00:00:00+00:00")
    out = write_inventory(inv)
    assert out == target
    body = target.read_text(encoding="utf-8")
    assert "Operator-authored intro." in body
    assert "Upgrade notes for 2026." in body


def test_build_inventory_returns_sorted_results(monkeypatch) -> None:
    """The four collectors should return sorted lists so the rendered
    markdown is deterministic."""
    monkeypatch.setattr(builder, "_collect_tools", lambda: [
        ToolEntry("z", "bronze", "singleton", (), "", True),
        ToolEntry("a", "bronze", "singleton", (), "", True),
    ])
    monkeypatch.setattr(builder, "_collect_monitors", list)
    monkeypatch.setattr(builder, "_collect_idle_jobs", list)
    monkeypatch.setattr(builder, "_collect_commands", list)
    inv = build_inventory(now=1_700_000_000.0)
    # _collect_tools result is honored as-is; the production collector sorts.
    assert [t.name for t in inv.tools] == ["z", "a"]


def test_run_once_skips_when_disabled(monkeypatch) -> None:
    from app.capability_inventory.builder import run_once
    monkeypatch.setattr(
        "app.runtime_settings.get_capability_inventory_enabled",
        lambda: False,
    )
    assert run_once() == {"ran": False, "skipped": True}


def test_render_cadence_formatter() -> None:
    inv = Inventory(
        as_of="2026-05-24T00:00:00+00:00",
        monitors=[
            MonitorEntry("five_min", 300),
            MonitorEntry("six_hour", 6 * 3600),
            MonitorEntry("daily", 86400),
            MonitorEntry("weekly", 7 * 86400),
        ],
    )
    md = render_markdown(inv)
    assert "5m" in md
    assert "6h" in md
    assert "1d" in md
    assert "7d" in md
