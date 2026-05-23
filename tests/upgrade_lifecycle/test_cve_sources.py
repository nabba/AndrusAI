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
