"""Tests for app.upgrade_lifecycle.cve_sources (P2#a).

PROGRAM §63.9 (P2 hardening). Covers:

  1. Primary returns results → secondary still queried, results merged
  2. Primary fails → secondary still runs (graceful degradation)
  3. Secondary fails → primary results returned untouched
  4. Both fail → empty dict
  5. Merge deduplicates by CVE id
  6. Divergence detection: same package, different CVE sets
  7. GitHub Advisory adapter handles non-list response gracefully
  8. GitHub Advisory adapter normalizes CVE rows
  9. Empty package list returns empty
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.upgrade_lifecycle import cve_sources as cs


def _osv_row(cve_id: str, summary: str = "test") -> dict[str, Any]:
    return {"id": cve_id, "summary": summary}


# ── 1: Both succeed, merge ─────────────────────────────────────────────


def test_both_sources_succeed_and_merge():
    primary = {"starlette": [_osv_row("CVE-2026-0001")]}
    secondary = {"starlette": [_osv_row("CVE-2026-0002")]}
    merged, divergent = cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=lambda _: primary,
        secondary_runner=lambda _: secondary,
    )
    ids = {row["id"] for row in merged["starlette"]}
    assert ids == {"CVE-2026-0001", "CVE-2026-0002"}
    assert "starlette" in divergent


# ── 2: Primary fails, secondary still runs ─────────────────────────────


def test_primary_failure_secondary_still_runs():
    def _exploding(_pkgs):
        raise RuntimeError("simulated")
    secondary = {"starlette": [_osv_row("CVE-2026-0002")]}
    merged, _ = cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=_exploding,
        secondary_runner=lambda _: secondary,
    )
    assert merged == {"starlette": [_osv_row("CVE-2026-0002")]}


# ── 3: Secondary fails, primary survives ───────────────────────────────


def test_secondary_failure_primary_returned():
    primary = {"starlette": [_osv_row("CVE-2026-0001")]}
    def _exploding(_pkgs):
        raise RuntimeError("simulated")
    merged, divergent = cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=lambda _: primary,
        secondary_runner=_exploding,
    )
    assert merged == {"starlette": [_osv_row("CVE-2026-0001")]}
    # No divergence reported when one source is silent
    assert divergent == ["starlette"]   # secondary empty == divergence


# ── 4: Both fail ───────────────────────────────────────────────────────


def test_both_fail_returns_empty():
    def _exploding(_pkgs):
        raise RuntimeError("simulated")
    merged, divergent = cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=_exploding,
        secondary_runner=_exploding,
    )
    assert merged == {}
    assert divergent == []


# ── 5: Dedup by CVE id ─────────────────────────────────────────────────


def test_dedup_by_cve_id():
    primary = {"starlette": [_osv_row("CVE-X")]}
    secondary = {"starlette": [_osv_row("CVE-X")]}   # same id
    merged, _ = cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=lambda _: primary,
        secondary_runner=lambda _: secondary,
    )
    assert len(merged["starlette"]) == 1


# ── 6: Divergence — different CVE sets per package ─────────────────────


def test_divergence_reported_when_one_finds_what_other_misses():
    primary = {"starlette": [_osv_row("CVE-A")]}
    secondary = {"starlette": [_osv_row("CVE-B")]}
    _, divergent = cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=lambda _: primary,
        secondary_runner=lambda _: secondary,
    )
    assert "starlette" in divergent


def test_no_divergence_when_both_empty():
    merged, divergent = cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=lambda _: {},
        secondary_runner=lambda _: {},
    )
    assert merged == {}
    assert divergent == []


# ── 7-8: GitHub Advisory adapter ───────────────────────────────────────


def test_github_adapter_handles_non_list_response():
    """API returning {error: ...} instead of a list → empty dict."""
    def _bad_fetch(_url):
        return b'{"error": "rate limited"}'
    out = cs._github_advisory_fetch(
        [("starlette", "0.52.1")], fetcher=_bad_fetch,
    )
    assert out == {}


def test_github_adapter_normalizes_rows():
    body = json.dumps([
        {"cve_id": "CVE-2026-9999", "summary": "starlette XSS",
         "severity": "high"},
        {"ghsa_id": "GHSA-abcd", "summary": "edge case"},
        {"id": "WITHOUT_CVE", "summary": "no key"},   # has 'id' fallback
    ]).encode("utf-8")
    out = cs._github_advisory_fetch(
        [("starlette", "0.52.1")], fetcher=lambda _: body,
    )
    rows = out["starlette"]
    ids = {r["id"] for r in rows}
    assert "CVE-2026-9999" in ids
    assert "GHSA-abcd" in ids
    assert all(r["source"] == "github_advisory" for r in rows)


# ── 9: Empty package list ──────────────────────────────────────────────


def test_empty_package_list():
    merged, divergent = cs.query_with_fallback(
        [],
        primary_runner=lambda _: {"pkg": [_osv_row("CVE-X")]},
        secondary_runner=lambda _: {},
    )
    assert merged == {}
    assert divergent == []


# ── 10-12: Continuity-ledger emission on divergence ────────────────────
#
# SubIA audit Round 2 Investigation B: divergence must surface to the
# identity ledger so annual reflection's ``summarise_drift`` Counter
# picks up CVE-source-health drift year-over-year.


def _install_fake_record_event(monkeypatch):
    """Patch app.identity.continuity_ledger.record_event with a captor.

    The lazy `from ... import record_event` inside the helper re-binds
    on every call, so attribute-level monkeypatching is picked up.
    """
    captured: list[dict[str, Any]] = []

    def _fake_record(*, kind, actor, summary, detail=None, **_kw):
        captured.append({
            "kind": kind, "actor": actor,
            "summary": summary, "detail": dict(detail or {}),
        })
        return True

    from app.identity import continuity_ledger as cl
    monkeypatch.setattr(cl, "record_event", _fake_record)
    return captured


def test_divergence_emits_ledger_event(monkeypatch):
    captured = _install_fake_record_event(monkeypatch)
    primary = {"starlette": [_osv_row("CVE-A"), _osv_row("CVE-COMMON")]}
    secondary = {"starlette": [_osv_row("CVE-B"), _osv_row("CVE-COMMON")]}
    cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=lambda _: primary,
        secondary_runner=lambda _: secondary,
    )
    assert len(captured) == 1
    ev = captured[0]
    assert ev["kind"] == "ecosystem_snapshot"
    assert ev["actor"] == "upgrade_lifecycle.cve_sources"
    assert "starlette" in ev["summary"]
    d = ev["detail"]
    assert d["subkind"] == "cve_source_divergence"
    assert d["package"] == "starlette"
    assert d["osv_finding"] == ["CVE-A", "CVE-COMMON"]
    assert d["github_finding"] == ["CVE-B", "CVE-COMMON"]
    assert d["only_osv"] == ["CVE-A"]
    assert d["only_github"] == ["CVE-B"]


def test_no_divergence_no_ledger_event(monkeypatch):
    captured = _install_fake_record_event(monkeypatch)
    same = {"starlette": [_osv_row("CVE-A")]}
    cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=lambda _: same,
        secondary_runner=lambda _: same,
    )
    assert captured == []


def test_ledger_emit_failure_does_not_break_merge(monkeypatch):
    """The CVE-source merge must NEVER fail because the ledger emit
    raised — failure isolation is the load-bearing invariant."""
    def _exploding_record(**_kw):
        raise RuntimeError("ledger disk full")

    from app.identity import continuity_ledger as cl
    monkeypatch.setattr(cl, "record_event", _exploding_record)

    primary = {"starlette": [_osv_row("CVE-A")]}
    secondary = {"starlette": [_osv_row("CVE-B")]}
    merged, divergent = cs.query_with_fallback(
        [("starlette", "0.52.1")],
        primary_runner=lambda _: primary,
        secondary_runner=lambda _: secondary,
    )
    # Merge survived the ledger explosion.
    assert "starlette" in merged
    assert {r["id"] for r in merged["starlette"]} == {"CVE-A", "CVE-B"}
    assert divergent == ["starlette"]
