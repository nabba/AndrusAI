"""Tests for app.upgrade_lifecycle.changelog_fetcher (U1).

PROGRAM §62. Covers:

  1.  PyPI metadata parse path (success + missing description)
  2.  GitHub releases parse path
  3.  Owner/repo extraction from project_urls
  4.  releases_between filter respects from/to bounds
  5.  Version normalization (strip v prefix)
  6.  LLM JSON parse — code-fence tolerance + strict-dict requirement
  7.  extract_for_package end-to-end with injected fetchers + LLM stub
  8.  Idempotent re-extraction (already_extracted dedup)
  9.  Hash chain integrity across multiple appends
  10. Chain-verify catches tampering
  11. run_one_batch respects max_per_batch
  12. Master switch OFF returns None without side effects
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.upgrade_lifecycle import changelog_fetcher as cf
from app.upgrade_lifecycle.protocol import Capability


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


@pytest.fixture
def enabled(monkeypatch: pytest.MonkeyPatch):
    """Force the master switch ON regardless of runtime_settings state."""
    monkeypatch.setattr(cf, "_enabled", lambda: True)


@pytest.fixture
def fake_llm_returning():
    """Return a builder factory that produces an llm stub with a fixed reply."""
    def _make(reply: str):
        class _LLM:
            def __init__(self, reply: str) -> None:
                self._reply = reply
                self.calls = 0

            def call(self, _messages):
                self.calls += 1
                return self._reply

        llm = _LLM(reply)
        return lambda: llm, llm
    return _make


# ── Helpers ──────────────────────────────────────────────────────────────


def _good_llm_reply() -> str:
    return json.dumps({
        "new_features": [
            "Added asyncio.TaskGroup for structured concurrency",
            "New http.Client supports streaming uploads",
        ],
        "deprecations": ["asyncio.gather() with return_exceptions=True"],
        "breaking_changes": ["Removed legacy Server.start_loop(); use run()"],
        "security_fixes": ["CVE-2026-0001: input validation in parse_url"],
        "perf_notes": ["json.loads 1.4x faster for nested dicts"],
        "notes": "Release notes are sparse for this bump.",
    })


def _meta_fetcher_for(project_urls=None, description=""):
    md = {"info": {"description": description, "project_urls": project_urls or {}}}
    return lambda pkg: md


def _releases_for(release_list):
    return lambda fv, tv: release_list


# ── 1: PyPI metadata fetch shape ────────────────────────────────────────


def test_github_owner_repo_extracted_from_project_urls():
    md = {
        "info": {
            "home_page": "",
            "project_urls": {
                "Source": "https://github.com/encode/starlette",
                "Docs": "https://www.starlette.io/",
            },
        }
    }
    assert cf._github_owner_repo(md) == ("encode", "starlette")


def test_github_owner_repo_handles_git_suffix():
    md = {"info": {"project_urls": {"Repo": "https://github.com/foo/bar.git"}}}
    assert cf._github_owner_repo(md) == ("foo", "bar")


def test_github_owner_repo_returns_none_when_absent():
    assert cf._github_owner_repo({"info": {"project_urls": {}}}) is None


# ── 2 + 4 + 5: Version normalization + releases filter ──────────────────


def test_normalize_version_strips_v_prefix():
    assert cf._normalize_version("v1.2.3") == "1.2.3"
    assert cf._normalize_version("  1.2.3 ") == "1.2.3"


def test_releases_between_filters_inclusive_to_exclusive_from():
    releases = [
        {"tag_name": "v1.0.0", "published_at": "2020-01-01"},
        {"tag_name": "v1.1.0", "published_at": "2020-06-01"},
        {"tag_name": "v1.2.0", "published_at": "2021-01-01"},
        {"tag_name": "v2.0.0", "published_at": "2022-01-01"},
    ]
    out = cf._releases_between(
        releases, from_version="1.0.0", to_version="1.2.0",
    )
    tags = [r["tag_name"] for r in out]
    # 1.0.0 excluded (from), 2.0.0 excluded (above to), 1.1.0 + 1.2.0 in.
    assert tags == ["v1.1.0", "v1.2.0"]


# ── 6: LLM JSON parse tolerance ─────────────────────────────────────────


def test_parse_strict_json_handles_code_fences():
    raw = "```json\n" + json.dumps({"new_features": ["a"]}) + "\n```"
    parsed = cf._parse_strict_json(raw)
    assert parsed == {"new_features": ["a"]}


def test_parse_strict_json_rejects_non_object():
    assert cf._parse_strict_json("[1, 2, 3]") is None
    assert cf._parse_strict_json("hello") is None
    assert cf._parse_strict_json("") is None


def test_coerce_str_list_caps_length_and_skips_non_strings():
    out = cf._coerce_str_list(["short", 42, None, "a" * 1000])
    assert out[0] == "short"
    assert len(out) == 2  # int and None dropped
    assert len(out[1]) == 200  # capped


# ── 7: End-to-end extraction with all sources injected ──────────────────


def test_extract_for_package_happy_path(isolated_dir, enabled, fake_llm_returning):
    builder, _ = fake_llm_returning(_good_llm_reply())
    cap = cf.extract_for_package(
        "starlette", "0.52.1", "1.0.1",
        metadata_fetcher=_meta_fetcher_for(
            project_urls={"Source": "https://github.com/encode/starlette"},
        ),
        releases_fetcher=_releases_for([
            {"tag_name": "v1.0.0", "body": "First major release",
             "published_at": "2026-01-01"},
            {"tag_name": "v1.0.1", "body": "Patch", "published_at": "2026-01-15"},
        ]),
        llm_builder=builder,
    )

    assert cap is not None
    assert cap.package == "starlette"
    assert cap.from_version == "0.52.1"
    assert cap.to_version == "1.0.1"
    assert cap.source == "github_releases"
    assert "TaskGroup" in cap.new_features[0]
    assert cap.breaking_changes[0].startswith("Removed legacy")
    # Ledger row written
    path = cf._ledger_path("starlette")
    assert path.exists()
    rows = path.read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["prev_hash"] == cf._GENESIS_HASH
    assert row["hash"] != cf._GENESIS_HASH


def test_extract_falls_back_to_pypi_description_when_no_github(
    isolated_dir, enabled, fake_llm_returning,
):
    builder, _ = fake_llm_returning(_good_llm_reply())
    cap = cf.extract_for_package(
        "tinypkg", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(
            project_urls={},
            description="## Changelog\n\nv2.0 — added X, removed Y.",
        ),
        releases_fetcher=lambda fv, tv: [],   # explicit empty so GH path skipped
        llm_builder=builder,
    )
    assert cap is not None
    assert cap.source == "pypi"


def test_extract_returns_none_when_llm_returns_garbage(
    isolated_dir, enabled, fake_llm_returning,
):
    builder, _ = fake_llm_returning("not json at all")
    cap = cf.extract_for_package(
        "x", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="some text"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    assert cap is None


def test_extract_returns_none_when_no_source_material(
    isolated_dir, enabled, fake_llm_returning,
):
    builder, _ = fake_llm_returning(_good_llm_reply())
    cap = cf.extract_for_package(
        "x", "1.0", "2.0",
        metadata_fetcher=lambda pkg: None,
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    assert cap is None


# ── 8: Idempotent re-extraction ─────────────────────────────────────────


def test_already_extracted_dedup_blocks_second_call(
    isolated_dir, enabled, fake_llm_returning,
):
    builder, llm = fake_llm_returning(_good_llm_reply())
    cap1 = cf.extract_for_package(
        "starlette", "0.52.1", "1.0.1",
        metadata_fetcher=_meta_fetcher_for(
            project_urls={"Source": "https://github.com/encode/starlette"},
        ),
        releases_fetcher=_releases_for([
            {"tag_name": "v1.0.1", "body": "Patch", "published_at": "2026-01-15"},
        ]),
        llm_builder=builder,
    )
    assert cap1 is not None
    assert llm.calls == 1

    # Second call: dedup short-circuits — no LLM invocation, no new row.
    cap2 = cf.extract_for_package(
        "starlette", "0.52.1", "1.0.1",
        metadata_fetcher=_meta_fetcher_for(
            project_urls={"Source": "https://github.com/encode/starlette"},
        ),
        releases_fetcher=_releases_for([
            {"tag_name": "v1.0.1", "body": "Patch", "published_at": "2026-01-15"},
        ]),
        llm_builder=builder,
    )
    assert cap2 is None
    assert llm.calls == 1   # LLM not re-invoked

    # Exactly one row on disk.
    rows = cf._ledger_path("starlette").read_text().splitlines()
    assert len(rows) == 1


# ── 9: Hash chain integrity across multiple appends ─────────────────────


def test_hash_chain_links_correctly_across_multiple_packages_and_versions(
    isolated_dir, enabled, fake_llm_returning,
):
    builder, _ = fake_llm_returning(_good_llm_reply())

    # Two versions of one package
    cf.extract_for_package(
        "alpha", "1.0", "1.1",
        metadata_fetcher=_meta_fetcher_for(description="v1.1 notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    cf.extract_for_package(
        "alpha", "1.1", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="v2.0 notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )

    path = cf._ledger_path("alpha")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    assert len(rows) == 2
    # Row 1 — prev_hash = genesis
    assert rows[0]["prev_hash"] == cf._GENESIS_HASH
    # Row 2 — prev_hash = row 1's hash
    assert rows[1]["prev_hash"] == rows[0]["hash"]

    ok, broken = cf.verify_chain("alpha")
    assert ok is True
    assert broken is None


# ── 10: Chain-verify catches tampering ──────────────────────────────────


def test_verify_chain_detects_payload_tampering(
    isolated_dir, enabled, fake_llm_returning,
):
    builder, _ = fake_llm_returning(_good_llm_reply())
    cf.extract_for_package(
        "alpha", "1.0", "1.1",
        metadata_fetcher=_meta_fetcher_for(description="v1.1 notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    path = cf._ledger_path("alpha")
    rows = path.read_text().splitlines()
    row = json.loads(rows[0])
    # Tamper with payload but leave hash field intact.
    row["payload"]["notes"] = "tampered text"
    path.write_text(json.dumps(row) + "\n")

    ok, broken_at = cf.verify_chain("alpha")
    assert ok is False
    assert broken_at == 0


# ── 11: run_one_batch respects max_per_batch ────────────────────────────


def test_run_one_batch_respects_max_per_batch(
    isolated_dir, enabled, fake_llm_returning,
):
    builder, llm = fake_llm_returning(_good_llm_reply())
    candidates = [
        (f"pkg-{i}", "1.0", "2.0") for i in range(6)
    ]
    summary = cf.run_one_batch(
        candidates,
        max_per_batch=3,
        metadata_fetcher=lambda pkg: {
            "info": {"description": "notes", "project_urls": {}},
        },
        releases_fetcher=lambda pkg, fv, tv: [],
        llm_builder=builder,
    )
    assert summary["extracted"] == 3
    # Only 3 LLM calls made (cap honored).
    assert llm.calls == 3


def test_run_one_batch_counts_dedup_separately(
    isolated_dir, enabled, fake_llm_returning,
):
    builder, _ = fake_llm_returning(_good_llm_reply())
    # First pass to populate one row.
    cf.run_one_batch(
        [("a", "1", "2")],
        metadata_fetcher=lambda pkg: {"info": {"description": "x", "project_urls": {}}},
        releases_fetcher=lambda pkg, fv, tv: [],
        llm_builder=builder,
    )
    # Second pass on the same candidate — dedup hit.
    summary = cf.run_one_batch(
        [("a", "1", "2"), ("b", "1", "2")],
        metadata_fetcher=lambda pkg: {"info": {"description": "y", "project_urls": {}}},
        releases_fetcher=lambda pkg, fv, tv: [],
        llm_builder=builder,
    )
    assert summary["skipped_dedup"] == 1
    assert summary["extracted"] == 1


# ── 12: Master switch OFF ───────────────────────────────────────────────


def test_master_switch_off_returns_none(isolated_dir, monkeypatch, fake_llm_returning):
    monkeypatch.setattr(cf, "_enabled", lambda: False)
    builder, llm = fake_llm_returning(_good_llm_reply())
    cap = cf.extract_for_package(
        "x", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="some text"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    assert cap is None
    assert llm.calls == 0


def test_run_one_batch_master_switch_off(isolated_dir, monkeypatch, fake_llm_returning):
    monkeypatch.setattr(cf, "_enabled", lambda: False)
    builder, _ = fake_llm_returning(_good_llm_reply())
    summary = cf.run_one_batch(
        [("a", "1", "2"), ("b", "1", "2")],
        metadata_fetcher=lambda pkg: {"info": {"description": "x", "project_urls": {}}},
        releases_fetcher=lambda pkg, fv, tv: [],
        llm_builder=builder,
    )
    assert summary["skipped_disabled"] == 2
    assert summary["extracted"] == 0


# ── Additional: read_capabilities round-trip ────────────────────────────


def test_read_capabilities_round_trip(isolated_dir, enabled, fake_llm_returning):
    builder, _ = fake_llm_returning(_good_llm_reply())
    cf.extract_for_package(
        "alpha", "1.0", "1.1",
        metadata_fetcher=_meta_fetcher_for(description="v1.1 notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    caps = cf.read_capabilities("alpha")
    assert len(caps) == 1
    cap = caps[0]
    assert isinstance(cap, Capability)
    assert cap.package == "alpha"
    assert cap.new_features == (
        "Added asyncio.TaskGroup for structured concurrency",
        "New http.Client supports streaming uploads",
    )
    assert cap.security_fixes[0].startswith("CVE-2026")
