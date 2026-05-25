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
        "license_change": "",
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


# ── P1#c: Monthly budget gate ───────────────────────────────────────────


def test_extraction_records_budget_attempt(isolated_dir, enabled, fake_llm_returning):
    """Every LLM call attempt (success OR failure) charges the budget."""
    builder, _ = fake_llm_returning(_good_llm_reply())
    cf.extract_for_package(
        "alpha", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="v2 notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    spend = cf.current_month_extraction_spend()
    assert spend > 0.0


def test_extraction_budget_exhausted_skips_extraction(
    isolated_dir, monkeypatch, fake_llm_returning,
):
    """When budget is exhausted, extract_for_package returns None
    without invoking the LLM."""
    monkeypatch.setattr(cf, "_enabled", lambda: True)
    monkeypatch.setattr(cf, "_monthly_budget_usd", lambda: 0.005)   # << cost-per
    builder, llm = fake_llm_returning(_good_llm_reply())
    cap = cf.extract_for_package(
        "alpha", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    assert cap is None
    assert llm.calls == 0   # gate prevented the LLM call


def test_extraction_budget_records_failure_too(
    isolated_dir, enabled, fake_llm_returning,
):
    """Even a parse-failure charges the budget."""
    builder, _ = fake_llm_returning("not valid json at all")
    cf.extract_for_package(
        "alpha", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    spend = cf.current_month_extraction_spend()
    assert spend > 0.0   # failure still charged


def test_license_change_field_extracted(isolated_dir, enabled, fake_llm_returning):
    """P2#c — license_change populated from LLM reply."""
    licensed_reply = json.dumps({
        "new_features": [], "deprecations": [],
        "breaking_changes": [], "security_fixes": [],
        "perf_notes": [],
        "license_change": "BSD-3 → AGPLv3",
        "notes": "",
    })
    builder, _ = fake_llm_returning(licensed_reply)
    cap = cf.extract_for_package(
        "spicy", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    assert cap is not None
    assert cap.license_change == "BSD-3 → AGPLv3"


def test_license_change_round_trips_through_ledger(
    isolated_dir, enabled, fake_llm_returning,
):
    """P2#c — license_change persists + reads back from the ledger."""
    licensed_reply = json.dumps({
        "new_features": [], "deprecations": [],
        "breaking_changes": [], "security_fixes": [],
        "perf_notes": [],
        "license_change": "MIT → SSPL",
        "notes": "",
    })
    builder, _ = fake_llm_returning(licensed_reply)
    cf.extract_for_package(
        "spicy", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    caps = cf.read_capabilities("spicy")
    assert len(caps) == 1
    assert caps[0].license_change == "MIT → SSPL"


# ── A5-P1: PyPI fallback via GitHub ────────────────────────────────────


def test_github_repo_cached_on_successful_pypi(isolated_dir):
    """Cache write happens automatically when PyPI metadata yields a repo URL."""
    md = {
        "info": {
            "project_urls": {"Source": "https://github.com/encode/starlette"},
        },
        "releases": {},
    }
    cf._maybe_cache_github_repo("starlette", md)
    cached = cf._cached_github_repo("starlette")
    assert cached == ("encode", "starlette")


def test_no_cache_when_pypi_has_no_repo_url(isolated_dir):
    md = {"info": {"project_urls": {}}}
    cf._maybe_cache_github_repo("ghostpkg", md)
    assert cf._cached_github_repo("ghostpkg") is None


def test_github_fallback_returns_none_when_no_cached_repo(isolated_dir, monkeypatch):
    """Without prior PyPI success, the fallback can't find the repo."""
    # No cache populated.
    result = cf._fetch_pypi_metadata_via_github("nevermet-pkg")
    assert result is None


def test_github_fallback_synthesizes_pypi_shape_when_cached(
    isolated_dir, monkeypatch,
):
    """With a cached repo + GitHub releases reachable, the fallback
    returns a dict shaped like real PyPI metadata."""
    # Seed the cache.
    cf._maybe_cache_github_repo("starlette", {
        "info": {
            "project_urls": {"Source": "https://github.com/encode/starlette"},
        },
    })
    # Stub GitHub releases.
    monkeypatch.setattr(cf, "_fetch_github_releases", lambda owner, repo, limit=30: [
        {"tag_name": "v1.0.1", "published_at": "2026-01-15T10:00:00Z"},
        {"tag_name": "v0.52.1", "published_at": "2025-08-10T08:00:00Z"},
    ])
    result = cf._fetch_pypi_metadata_via_github("starlette")
    assert result is not None
    # PyPI shape: info.project_urls + releases keyed by tag.
    info = result.get("info") or {}
    assert info.get("_synthesized_from") == "github_releases"
    assert "github.com/encode/starlette" in info["project_urls"]["Source"]
    releases = result.get("releases") or {}
    # Tags stripped of `v` prefix per _normalize_version
    assert "1.0.1" in releases
    assert "0.52.1" in releases
    assert releases["1.0.1"][0]["upload_time"].startswith("2026-01-15")


def test_pypi_failure_falls_through_to_github(isolated_dir, monkeypatch):
    """When PyPI's HTTP call raises, the fallback synthesizes from GitHub."""
    cf._maybe_cache_github_repo("starlette", {
        "info": {
            "project_urls": {"Source": "https://github.com/encode/starlette"},
        },
    })
    def _exploding(_url, timeout=None):
        raise urllib_error_urlerror("simulated pypi down")
    import urllib.error as urllib_error_urlerror_mod
    urllib_error_urlerror = urllib_error_urlerror_mod.URLError

    monkeypatch.setattr(cf, "_budgeted_get", _exploding)
    monkeypatch.setattr(cf, "_fetch_github_releases", lambda owner, repo, limit=30: [
        {"tag_name": "1.0.1", "published_at": "2026-01-15T10:00:00Z"},
    ])
    result = cf._fetch_pypi_metadata("starlette")
    assert result is not None
    assert (result.get("info") or {}).get("_synthesized_from") == "github_releases"


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


# ── Gap 3: PhenomenalLanguageLinter at the producer ─────────────────────


@pytest.fixture
def fake_llm_sequence():
    """Return a builder that yields a sequence of canned replies — one per
    call. Used to model retry-after-HARD_FAIL paths."""
    def _make(replies: list[str]):
        class _SeqLLM:
            def __init__(self, replies: list[str]) -> None:
                self._replies = list(replies)
                self.calls = 0

            def call(self, _messages):
                self.calls += 1
                if not self._replies:
                    return self._replies and "" or ""  # exhausted
                return self._replies.pop(0)

        llm = _SeqLLM(replies)
        return lambda: llm, llm
    return _make


def _dirty_llm_reply() -> str:
    """Reply containing a HARD_FAIL phrase ('I am curious') in notes."""
    return json.dumps({
        "new_features": ["Added asyncio.TaskGroup for structured concurrency"],
        "deprecations": [],
        "breaking_changes": [],
        "security_fixes": [],
        "perf_notes": [],
        "license_change": "",
        "notes": "I am curious about whether this release improves throughput.",
    })


def _clean_llm_reply() -> str:
    return json.dumps({
        "new_features": ["Added asyncio.TaskGroup for structured concurrency"],
        "deprecations": [],
        "breaking_changes": [],
        "security_fixes": [],
        "perf_notes": [],
        "license_change": "",
        "notes": "The release improves throughput on nested-dict workloads.",
    })


def test_linter_retry_succeeds_on_second_call(
    isolated_dir, enabled, fake_llm_sequence,
):
    """Gap 3 — first reply contains 'I am curious' (HARD_FAIL on notes
    field); the retry returns a clean reply; the stored Capability has
    the clean notes verbatim."""
    builder, llm = fake_llm_sequence([_dirty_llm_reply(), _clean_llm_reply()])
    cap = cf.extract_for_package(
        "alpha", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="release notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    assert cap is not None
    assert llm.calls == 2                       # one retry happened
    assert "I am curious" not in cap.notes
    assert "improves throughput" in cap.notes
    assert "TaskGroup" in cap.new_features[0]


def test_linter_failure_after_retry_blanks_only_failing_field(
    isolated_dir, enabled, fake_llm_sequence, monkeypatch,
):
    """Gap 3 — both first AND retry replies contain HARD_FAIL in notes;
    only the notes field is blanked. Clean siblings (new_features)
    survive verbatim. Telemetry row is recorded."""
    recorded: list[dict] = []
    def _capture(**kwargs):
        recorded.append(kwargs)
        return True
    monkeypatch.setattr(
        "app.threads.linter_telemetry.record_rejection", _capture,
    )

    builder, llm = fake_llm_sequence([_dirty_llm_reply(), _dirty_llm_reply()])
    cap = cf.extract_for_package(
        "beta", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    assert cap is not None
    assert llm.calls == 2                       # exactly one retry
    # Clean siblings preserved
    assert cap.new_features == (
        "Added asyncio.TaskGroup for structured concurrency",
    )
    # Failing field blanked
    assert cap.notes == ""
    # Telemetry recorded with capability-scoped thread_id
    assert len(recorded) == 1
    assert recorded[0]["thread_id"] == "capability:beta:2.0"
    assert recorded[0]["violations"]            # non-empty


def test_linter_bounds_retries_to_one_even_on_persistent_failure(
    isolated_dir, enabled, fake_llm_sequence,
):
    """Gap 3 — cost cap. Even with three dirty replies queued, only the
    first two are consumed (one initial + one retry); the third remains
    in the queue."""
    builder, llm = fake_llm_sequence(
        [_dirty_llm_reply(), _dirty_llm_reply(), _dirty_llm_reply()],
    )
    cap = cf.extract_for_package(
        "gamma", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    assert cap is not None
    assert llm.calls == 2


def test_linter_clean_reply_does_not_trigger_retry(
    isolated_dir, enabled, fake_llm_sequence,
):
    """Gap 3 — when the first reply is clean, no retry; LLM called once."""
    builder, llm = fake_llm_sequence([_clean_llm_reply()])
    cap = cf.extract_for_package(
        "delta", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(description="notes"),
        releases_fetcher=lambda fv, tv: [],
        llm_builder=builder,
    )
    assert cap is not None
    assert llm.calls == 1
    assert "improves throughput" in cap.notes


# ── Gap 5: CHANGELOG.md URL adapter ─────────────────────────────────────


_FAKE_CHANGELOG_MD = """# Changelog

## 2.0.0 (2026-01-15)

* Added asyncio.TaskGroup support for structured concurrency
* Removed legacy Server.start_loop(); use Server.run() instead

## 1.9.5 (2025-12-01)

* Bugfix: parse_url crash on empty input

## 1.0 (2025-01-01)

* Initial release
"""

_FAKE_CHANGELOG_HTML = """<!DOCTYPE html>
<html><body>
<h1>Changelog</h1>
<h2>2.0.0 (2026-01-15)</h2>
<ul>
<li>Added asyncio.TaskGroup for structured concurrency</li>
<li>Removed legacy Server.start_loop()</li>
</ul>
<h2>1.9.5</h2>
<ul><li>Bugfix: parse_url</li></ul>
<h2>1.0</h2>
<ul><li>Initial release</li></ul>
</body></html>
"""


def test_changelog_url_extraction_finds_standard_keys():
    """Gap 5 — accept Changelog / Changes / Release Notes / History."""
    for key in ("Changelog", "Changes", "Release Notes", "History",
                "release-notes", "CHANGELOG"):
        md = {"info": {"project_urls": {key: "https://example.org/changes"}}}
        assert cf._changelog_url_from_pypi(md) == "https://example.org/changes"


def test_changelog_url_extraction_ignores_non_http_values():
    md = {"info": {"project_urls": {"Changelog": "not-a-url"}}}
    assert cf._changelog_url_from_pypi(md) is None


def test_changelog_url_extraction_returns_none_when_absent():
    md = {"info": {"project_urls": {"Source": "https://github.com/x/y"}}}
    assert cf._changelog_url_from_pypi(md) is None


def test_slice_changelog_versions_markdown_inclusive_to_exclusive_from():
    """Section [to=2.0.0 .. from=1.0) bounded — 1.9.5 included, 1.0 excluded."""
    section = cf._slice_changelog_versions(
        _FAKE_CHANGELOG_MD, from_version="1.0", to_version="2.0.0",
    )
    assert "TaskGroup" in section
    assert "1.9.5" in section
    assert "Initial release" not in section


def test_slice_changelog_versions_returns_empty_when_to_missing():
    """No heading matches to_version → empty string (caller falls back)."""
    section = cf._slice_changelog_versions(
        _FAKE_CHANGELOG_MD, from_version="1.0", to_version="9.9.9",
    )
    assert section == ""


def test_slice_changelog_versions_handles_v_prefix_and_brackets():
    text = """## [v2.1.0]
- Foo

## v1.0.0
- Bar
"""
    section = cf._slice_changelog_versions(text, "1.0.0", "2.1.0")
    assert "Foo" in section
    assert "Bar" not in section


def test_strip_html_preserves_headings_for_slicing():
    text = cf._strip_html(_FAKE_CHANGELOG_HTML)
    # Headings re-emitted with markdown markers
    assert "## 2.0.0" in text
    assert "TaskGroup" in text
    # script / style content excluded (none in fixture; just verify clean)
    assert "<script" not in text


def test_strip_html_drops_script_and_style_content():
    text = cf._strip_html(
        "<html><script>alert('x');</script><h2>1.0</h2>"
        "<style>body{color:red}</style><p>real</p></html>"
    )
    assert "alert" not in text
    assert "color:red" not in text
    assert "real" in text
    assert "1.0" in text


def test_fetch_changelog_section_success_markdown(monkeypatch):
    """Gap 5 — full path with monkeypatched HTTP returning markdown."""
    captured_urls: list[str] = []
    def _fake_get(url, timeout):
        captured_urls.append(url)
        return _FAKE_CHANGELOG_MD.encode("utf-8")
    monkeypatch.setattr(cf, "_budgeted_changelog_get", _fake_get)

    md = {"info": {"project_urls": {
        "Changelog": "https://example.org/CHANGELOG.md",
    }}}
    section = cf._fetch_changelog_section(md, "1.0", "2.0.0")
    assert section is not None
    assert "TaskGroup" in section
    assert captured_urls == ["https://example.org/CHANGELOG.md"]


def test_fetch_changelog_section_returns_none_when_no_url():
    md = {"info": {"project_urls": {}}}
    assert cf._fetch_changelog_section(md, "1.0", "2.0.0") is None


def test_fetch_changelog_section_returns_none_on_fetch_failure(monkeypatch):
    def _fake_get(url, timeout):
        raise TimeoutError("simulated")
    monkeypatch.setattr(cf, "_budgeted_changelog_get", _fake_get)
    md = {"info": {"project_urls": {"Changelog": "https://example.org/x"}}}
    assert cf._fetch_changelog_section(md, "1.0", "2.0") is None


def test_assemble_excerpt_prepends_changelog_section_to_releases():
    """Gap 5 — when both changelog + GitHub releases present, changelog
    is included first and label upgrades to 'changelog_url'."""
    text, label = cf._assemble_excerpt(
        package="x", from_version="1.0", to_version="2.0",
        pypi_metadata={"info": {"description": "pypi"}},
        github_releases=[{"tag_name": "v2.0", "body": "release body",
                          "published_at": "2026-01-15"}],
        changelog_section="## 2.0\nAdded structured concurrency",
    )
    assert label == "changelog_url"
    assert "structured concurrency" in text
    assert "release body" in text                # not lost
    pos_cl = text.find("structured concurrency")
    pos_gh = text.find("release body")
    assert pos_cl < pos_gh                       # changelog comes first


def test_assemble_excerpt_falls_back_when_no_changelog():
    text, label = cf._assemble_excerpt(
        package="x", from_version="1.0", to_version="2.0",
        pypi_metadata=None,
        github_releases=[{"tag_name": "v2.0", "body": "release body",
                          "published_at": "2026-01-15"}],
        changelog_section=None,
    )
    assert label == "github_releases"
    assert "release body" in text


def test_extract_for_package_uses_changelog_when_releases_empty(
    isolated_dir, enabled, fake_llm_returning,
):
    """Gap 5 — full pipeline: changelog adapter supplies the only
    content; capability is stored with source='changelog_url'."""
    builder, _ = fake_llm_returning(_good_llm_reply())
    cap = cf.extract_for_package(
        "lonely", "1.0", "2.0.0",
        metadata_fetcher=_meta_fetcher_for(
            project_urls={"Changelog": "https://example.org/c.md"},
        ),
        releases_fetcher=lambda fv, tv: [],
        changelog_fetcher=lambda md, fv, tv: "## 2.0.0\nAdded asyncio.TaskGroup",
        llm_builder=builder,
    )
    assert cap is not None
    assert cap.source == "changelog_url"


def test_extract_for_package_changelog_adapter_failure_does_not_block(
    isolated_dir, enabled, fake_llm_returning,
):
    """Gap 5 — changelog adapter raising must not break extraction;
    pipeline falls back to PyPI + GitHub paths."""
    builder, _ = fake_llm_returning(_good_llm_reply())
    def _exploding_changelog(md, fv, tv):
        raise RuntimeError("simulated changelog failure")
    cap = cf.extract_for_package(
        "robust", "1.0", "2.0",
        metadata_fetcher=_meta_fetcher_for(
            description="## Changelog\n\nv2.0 - added X",
        ),
        releases_fetcher=lambda fv, tv: [],
        changelog_fetcher=_exploding_changelog,
        llm_builder=builder,
    )
    assert cap is not None
    assert cap.source == "pypi"
