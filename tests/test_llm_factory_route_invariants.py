"""
test_llm_factory_route_invariants.py — Pinning tests for the
factory's "one source of truth" contract.

These tests pin the contract that emerged from the 2026-05-24 incident
(Anthropic 404 on ``anthropic/claude-sonnet-4-6`` reaching the user as
"Sorry, I had trouble understanding…"):

1. The catalog stores the LiteLLM-canonical prefixed model id; the
   factory derives the bare form for the native Anthropic SDK route.
   ``derived_id()`` is the single point where this happens.
2. Every catalog entry passes ``validate_entry`` — a shape-broken entry
   (provider/prefix mismatch) is caught at module load time before any
   construction is attempted.
3. The chain walker tries the resolver-picked model first, falls
   through to bootstrap survivors, and raises ``NoWorkingModelAvailable``
   if every candidate fails.  No silent drops to a generic
   "trouble understanding" reply.
4. The health cache short-circuits dead candidates within the failure
   TTL.

Tests are deliberately scoped to the factory + catalog + probe modules.
End-to-end orchestrator tests live elsewhere.
"""
from __future__ import annotations

import pytest

from app import llm_catalog
from app.llm_catalog import (
    CATALOG, _BOOTSTRAP_CATALOG,
    derived_id, validate_entry, fallback_chain,
)
from app import llm_factory
from app.llm_factory import (
    ConstructionFailed, NoWorkingModelAvailable,
    _construct_from_entry, _walk_chain, _chain_for_role,
)
from app import llm_factory_probe


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """Every test starts with an empty health cache.  Otherwise a prior
    mark_dead in one test would persist into the next and silently skew
    chain-walker behavior."""
    llm_factory_probe._reset_for_tests()
    yield
    llm_factory_probe._reset_for_tests()


# ── derived_id contract ────────────────────────────────────────────────

class TestDerivedIdContract:
    """The golden table: for every bootstrap survivor, the two routes
    return the exact shape each consumer expects."""

    def test_openrouter_litellm_preserves_full_id(self):
        entry = _BOOTSTRAP_CATALOG["deepseek-v3.2"]
        # OpenRouter calls go through LiteLLM with the
        # ``openrouter/...`` form — nothing to strip.
        assert derived_id(entry, "litellm") == "openrouter/deepseek/deepseek-chat"

    def test_openrouter_native_anthropic_strips_one_prefix(self):
        # Sending an OpenRouter entry down the native_anthropic route
        # is a misuse — but ``derived_id`` is shape-only, so it just
        # strips one prefix.  ``_construct_from_entry`` rejects the
        # misuse separately via the provider dispatch.
        entry = _BOOTSTRAP_CATALOG["deepseek-v3.2"]
        assert derived_id(entry, "native_anthropic") == "deepseek/deepseek-chat"

    def test_ollama_native_strips_ollama_chat_prefix(self):
        entry = _BOOTSTRAP_CATALOG["qwen3.5:35b-a3b-q4_K_M"]
        assert derived_id(entry, "native_anthropic") == "qwen3.5:35b-a3b-q4_K_M"

    def test_unknown_route_raises(self):
        entry = _BOOTSTRAP_CATALOG["claude-sonnet-4.6"]
        with pytest.raises(ValueError, match="unknown route"):
            derived_id(entry, "nope")

    def test_anthropic_openrouter_route_translates_version(self):
        # The Anthropic→OpenRouter translation: dash version separators
        # become dots, and the prefix changes from ``anthropic/`` to
        # ``openrouter/anthropic/``.  Pins the credit-exhausted failover
        # path's id-shape contract — previously this lived as a regex
        # in llm_factory._anthropic_to_openrouter_model_id (now deleted).
        entry = _BOOTSTRAP_CATALOG["claude-sonnet-4.6"]
        assert derived_id(entry, "openrouter") == "openrouter/anthropic/claude-sonnet-4.6"

    def test_openrouter_native_openrouter_route_is_identity(self):
        # An OpenRouter-provider entry already carries the correct
        # OpenRouter form; the route is identity.
        entry = _BOOTSTRAP_CATALOG["deepseek-v3.2"]
        assert derived_id(entry, "openrouter") == "openrouter/deepseek/deepseek-chat"

    def test_ollama_openrouter_route_refuses(self):
        # Ollama entries have no OpenRouter equivalent; refuse rather
        # than fabricate a malformed id.
        entry = _BOOTSTRAP_CATALOG["qwen3.5:35b-a3b-q4_K_M"]
        with pytest.raises(ValueError, match="no OpenRouter form known"):
            derived_id(entry, "openrouter")

    def test_ollama_native_route_strips_ollama_chat_prefix(self):
        # Symmetric with native_anthropic — for a hypothetical native
        # Ollama HTTP API call.
        entry = _BOOTSTRAP_CATALOG["qwen3.5:35b-a3b-q4_K_M"]
        assert derived_id(entry, "ollama_native") == "qwen3.5:35b-a3b-q4_K_M"


# ── validate_entry contract ───────────────────────────────────────────

class TestValidateEntry:
    """Shape validation catches the 2026-05-24 bug class at the catalog
    level — before the factory ever tries to construct."""

    def test_bootstrap_survivors_all_pass(self):
        """The catalog's own survival kit must be self-consistent.
        Any failure here means a programming error in the catalog."""
        for name, entry in _BOOTSTRAP_CATALOG.items():
            problems = validate_entry(name, entry)
            assert problems == [], (
                f"Bootstrap survivor {name!r} has shape problems: {problems}"
            )

    def test_anthropic_without_prefix_flagged(self):
        # The 2026-05-24 regression case in inverted form: if the
        # catalog were ever written with the bare id under
        # ``provider="anthropic"``, validate_entry must flag it.
        bad_entry = {
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",  # missing "anthropic/"
        }
        problems = validate_entry("bad", bad_entry)
        assert problems != []
        assert "anthropic/" in problems[0]
        assert "missing expected prefix" in problems[0]

    def test_provider_mismatch_flagged(self):
        # An entry whose provider field disagrees with its id prefix.
        bad_entry = {
            "provider": "openrouter",
            "model_id": "anthropic/claude-sonnet-4-6",
        }
        problems = validate_entry("bad", bad_entry)
        assert problems != []

    def test_missing_provider_flagged(self):
        bad_entry = {"model_id": "anthropic/claude-sonnet-4-6"}
        problems = validate_entry("bad", bad_entry)
        assert any("missing 'provider'" in p for p in problems)

    def test_missing_model_id_flagged(self):
        bad_entry = {"provider": "anthropic"}
        problems = validate_entry("bad", bad_entry)
        assert any("missing 'model_id'" in p for p in problems)

    def test_nested_slash_in_anthropic_id_flagged(self):
        bad_entry = {
            "provider": "anthropic",
            "model_id": "anthropic/some/nested/id",
        }
        problems = validate_entry("bad", bad_entry)
        assert any("nested slash" in p for p in problems)


# ── fallback_chain contract ────────────────────────────────────────────

class TestFallbackChain:
    def test_fallback_chain_returns_known_survivors(self):
        chain = fallback_chain("commander")
        # All three bootstrap survivors must appear.
        assert "claude-sonnet-4.6" in chain
        assert "deepseek-v3.2" in chain
        assert "qwen3.5:35b-a3b-q4_K_M" in chain

    def test_fallback_chain_role_independent_today(self):
        # The current implementation is intentionally role-independent;
        # this test pins that contract so a future role-keyed
        # implementation has to update the test consciously.
        assert fallback_chain("commander") == fallback_chain("vetting")
        assert fallback_chain("commander") == fallback_chain("default")


# ── _chain_for_role composition ────────────────────────────────────────

class TestChainForRole:
    def test_primary_prepended_then_survivors(self):
        chain = _chain_for_role("commander", primary="some-role-pick")
        assert chain[0] == "some-role-pick"
        # Bootstrap survivors follow.
        for survivor in fallback_chain("commander"):
            assert survivor in chain

    def test_primary_already_in_chain_not_duplicated_after_walker_dedup(self):
        # The walker dedups, so passing primary="claude-sonnet-4.6"
        # (already in the survivor chain) is harmless.
        chain = _chain_for_role("commander", primary="claude-sonnet-4.6")
        # Chain itself may have the duplicate; the walker dedups.
        assert chain[0] == "claude-sonnet-4.6"

    def test_no_primary_returns_survivors_only(self):
        chain = _chain_for_role("commander", primary=None)
        assert chain == fallback_chain("commander")


# ── health cache (probe) ──────────────────────────────────────────────

class TestHealthCache:
    def test_unknown_model_returns_none(self):
        assert llm_factory_probe.health_of("anthropic", "fake-model") is None

    def test_mark_dead_then_health_says_dead(self):
        llm_factory_probe.mark_dead("anthropic", "test-model", "404 not_found_error")
        rec = llm_factory_probe.health_of("anthropic", "test-model")
        assert rec is not None
        assert rec.is_alive is False
        assert "not_found_error" in rec.last_reason

    def test_mark_alive_after_dead_overwrites(self):
        llm_factory_probe.mark_dead("anthropic", "test-model", "tmp")
        llm_factory_probe.mark_alive("anthropic", "test-model")
        rec = llm_factory_probe.health_of("anthropic", "test-model")
        assert rec is not None
        assert rec.is_alive is True

    def test_classify_failure_matches_anthropic_404(self):
        # The 2026-05-24 incident message, verbatim from the production
        # error log:
        msg = (
            "Anthropic API call failed: Error code: 404 - "
            "{'type': 'error', 'error': {'type': 'not_found_error', "
            "'message': 'model: anthropic/claude-sonnet-4-6'}}"
        )
        result = llm_factory_probe.classify_failure(RuntimeError(msg))
        assert result is not None
        assert "not_found_error" in result

    def test_classify_failure_ignores_credit_exhausted(self):
        # Credit exhaustion is a separate concern — should NOT mark
        # the model dead.
        msg = (
            "Your credit balance is too low to access the Anthropic API."
        )
        assert llm_factory_probe.classify_failure(RuntimeError(msg)) is None

    def test_classify_failure_ignores_rate_limit(self):
        msg = "429 Too Many Requests rate limit exceeded"
        assert llm_factory_probe.classify_failure(RuntimeError(msg)) is None

    def test_classify_failure_matches_openrouter_model_not_found(self):
        msg = "OpenRouter: model not found: anthropic/claude-old"
        result = llm_factory_probe.classify_failure(RuntimeError(msg))
        assert result is not None


# ── Health cache persistence ──────────────────────────────────────────

class TestHealthCachePersistence:
    """Disk persistence so rolling deploys don't re-pay 404s."""

    def test_persist_and_reload_roundtrip(self, tmp_path, monkeypatch):
        # Point persistence at a tmp file and re-enable it (the autouse
        # fixture above disabled persistence for safety).
        persist_path = tmp_path / "health_cache.json"
        monkeypatch.setattr(llm_factory_probe, "_PERSIST_PATH", persist_path)
        monkeypatch.setattr(llm_factory_probe, "_persistence_disabled", False)

        llm_factory_probe.mark_dead(
            "anthropic", "claude-sonnet-4-6", "test 404",
        )
        assert persist_path.exists(), (
            "mark_dead must flush immediately (bypassing throttle) so "
            "rolling deploys pick up the dead mark on next boot"
        )

        # Simulate process restart: wipe in-memory, then reload.
        with llm_factory_probe._HEALTH_LOCK:
            llm_factory_probe._HEALTH.clear()
        assert llm_factory_probe.health_of("anthropic", "claude-sonnet-4-6") is None

        llm_factory_probe._load_from_disk()
        rec = llm_factory_probe.health_of("anthropic", "claude-sonnet-4-6")
        assert rec is not None
        assert rec.is_alive is False
        assert "404" in rec.last_reason

    def test_corrupt_file_treated_as_missing(self, tmp_path, monkeypatch):
        persist_path = tmp_path / "health_cache.json"
        persist_path.write_text("this is not json {{{{")
        monkeypatch.setattr(llm_factory_probe, "_PERSIST_PATH", persist_path)

        # Must not raise.
        llm_factory_probe._load_from_disk()
        # Cache stays empty.
        assert llm_factory_probe.health_of("anthropic", "anything") is None

    def test_persistent_failure_escalates_after_threshold(self, tmp_path, monkeypatch):
        """Closes the mark_dead → catalog-retirement loop.

        Three mark_dead within 24h of the same (provider, bare_id) must
        trigger ``_escalate_persistent_failure``, which files a CR via
        proposal_bridge and emits a continuity-ledger event.  This is
        the contract that makes a 60-second TTL into a durable signal.
        """
        # Re-enable disk persistence in a tmp dir.
        monkeypatch.setattr(
            llm_factory_probe, "_PERSIST_PATH", tmp_path / "health.json",
        )
        monkeypatch.setattr(
            llm_factory_probe, "_DEAD_MARKS_PATH", tmp_path / "dead_marks.jsonl",
        )
        monkeypatch.setattr(
            llm_factory_probe, "_ESCALATION_STATE_PATH", tmp_path / "escalations.json",
        )
        monkeypatch.setattr(llm_factory_probe, "_persistence_disabled", False)

        # Stub the proposal_bridge to observe the call.
        staged = []

        def fake_stage(*, source, signature, title, body_markdown, target_path, cooldown_days=7, coding_session_spec=None):  # noqa: ARG001
            staged.append({
                "source": source,
                "signature": signature,
                "title": title,
                "body": body_markdown,
            })
            class _S:
                pass
            return (_S(), True)

        from app.proposal_bridge import store as pb_store
        monkeypatch.setattr(pb_store, "stage", fake_stage)

        # Stub continuity ledger so the test doesn't write to its file.
        emitted_events = []

        def fake_record_event(**kw):
            emitted_events.append(kw)

        # The import happens lazily inside _escalate_persistent_failure,
        # so we have to monkeypatch the actual target module.
        try:
            from app.identity import continuity_ledger
            monkeypatch.setattr(continuity_ledger, "record_event", fake_record_event)
        except ImportError:
            # If the continuity ledger isn't importable in this test env,
            # the escalation still fires the CR; just skip the ledger
            # assertion.
            pass

        # First two marks: below threshold, no escalation.
        llm_factory_probe.mark_dead("anthropic", "test-model", "404 not_found_error")
        llm_factory_probe.mark_dead("anthropic", "test-model", "404 not_found_error")
        assert staged == [], "Below threshold: must NOT escalate"

        # Third mark crosses the threshold.
        llm_factory_probe.mark_dead("anthropic", "test-model", "404 not_found_error")
        assert len(staged) == 1, (
            "At threshold: must stage exactly one retirement CR"
        )
        proposal = staged[0]
        assert proposal["source"] == "llm_health_escalator"
        assert "anthropic" in proposal["signature"]
        assert "test-model" in proposal["signature"]
        assert "Retire anthropic/test-model" in proposal["title"]
        assert "404 not_found_error" in proposal["body"]
        assert "action: retire_catalog_entry" in proposal["body"]

        # Fourth mark within dedup window: must NOT re-stage.
        llm_factory_probe.mark_dead("anthropic", "test-model", "404 not_found_error")
        assert len(staged) == 1, (
            "Dedup: must not re-escalate the same (provider, bare_id) "
            "within _ESCALATION_DEDUP_DAYS"
        )

    def test_expired_records_dropped_on_load(self, tmp_path, monkeypatch):
        import json
        persist_path = tmp_path / "health_cache.json"
        # Construct an already-expired record.
        persist_path.write_text(json.dumps({
            "anthropic|stale-model": {
                "is_alive": False,
                "expires_at": 0.0,  # epoch 0 — definitively expired
                "last_reason": "stale",
            },
            "anthropic|fresh-model": {
                "is_alive": True,
                "expires_at": 9_999_999_999.0,  # far future
                "last_reason": "",
            },
        }))
        monkeypatch.setattr(llm_factory_probe, "_PERSIST_PATH", persist_path)

        llm_factory_probe._load_from_disk()
        # Stale dropped; fresh kept.
        assert llm_factory_probe.health_of("anthropic", "stale-model") is None
        fresh = llm_factory_probe.health_of("anthropic", "fresh-model")
        assert fresh is not None and fresh.is_alive is True


# ── AnthropicClientHandle (raw-SDK factory surface) ───────────────────

class TestAnthropicClientHandle:
    """The factory API the 22 bypass sites should be using."""

    def test_no_anthropic_sdk_imports_outside_factory(self):
        """The single-island invariant (OpenRouter+Ollama consolidation).

        After the consolidation, **OpenRouter and Ollama are the only LLM
        providers** and the factory (via ``chat_completion_for_role`` /
        ``create_specialist_llm``) is the only way to obtain an LLM.  The
        native Anthropic SDK survives in exactly ONE place — the
        computer-use vision island — because it needs the
        ``computer-use-2025-01-24`` beta that OpenRouter cannot proxy.

        Any OTHER file that imports / constructs the native Anthropic SDK
        has re-opened the dual-dialect surface the consolidation closed,
        and this test fails with the offending file:line.

        Sanctioned files (the island, and nothing else):
          * ``app/computer_use/``            — the vision UI-automation
            subsystem; calls the SDK with the ``computer_20250124`` tool.
          * ``app/tools/computer_use_tool.py`` — availability-check stub
            that does ``import anthropic`` purely to detect installation;
            never constructs a client.
        """
        import pathlib, re as _re

        sanctioned_prefixes = (
            "app/computer_use/",
            "app/tools/computer_use_tool.py",
        )

        # Bare ``import anthropic`` / ``from anthropic import …`` —
        # NOT ``from anthropic.types``, NOT ``import anthropic.utils``,
        # because typing-only imports don't construct clients.
        bare_import_pat = _re.compile(
            r"^\s*(?:from\s+anthropic\s+import|import\s+anthropic\s*(?:#|$))",
            _re.MULTILINE,
        )
        # Direct construction.
        construct_pat = _re.compile(
            r"\banthropic\.Anthropic\s*\(|"
            r"^\s*from\s+anthropic\s+import\s+(?:[^\n]*\b)?Anthropic\b",
            _re.MULTILINE,
        )

        violations: list[tuple[str, int, str]] = []
        app_root = pathlib.Path(__file__).resolve().parents[1] / "app"
        for py_file in app_root.rglob("*.py"):
            rel = py_file.relative_to(app_root.parent).as_posix()
            if any(rel.startswith(p) for p in sanctioned_prefixes):
                continue
            try:
                text = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for pat in (bare_import_pat, construct_pat):
                for m in pat.finditer(text):
                    line_no = text[:m.start()].count("\n") + 1
                    snippet = text[m.start():m.end()].strip()
                    violations.append((rel, line_no, snippet))

        if violations:
            formatted = "\n".join(
                f"  {f}:{ln}  →  {s}" for f, ln, s in violations[:20]
            )
            extra = f"\n  …and {len(violations) - 20} more" if len(violations) > 20 else ""
            pytest.fail(
                "SINGLE-ISLAND VIOLATION — native Anthropic SDK use "
                "outside the computer-use island.  Route LLM calls through "
                "``app.llm_factory.chat_completion_for_role(role, task_hint)`` "
                "(OpenRouter/Ollama) instead.\n\n"
                f"{formatted}{extra}\n\n"
                "If a NEW sanctioned bypass is truly unavoidable, add it to "
                "``sanctioned_prefixes`` in this test with a comment "
                "explaining the structural reason."
            )

    def test_idle_pause_skips_anthropic_in_chain_walker(self, monkeypatch):
        """When the total-cost-ceiling monitor engages the spend brake,
        Anthropic candidates are refused at construction time and the
        chain walker falls through to non-Anthropic alternatives."""
        # Patch the hoisted import in llm_factory's namespace — the
        # canonical pytest pattern is to patch where the function is
        # LOOKED UP, not where it's DEFINED.  The runtime_settings
        # module was imported at module top in llm_factory; patching
        # runtime_settings.get_idle_pause_due_to_budget would NOT
        # affect the captured reference.
        monkeypatch.setattr(
            llm_factory, "get_idle_pause_due_to_budget", lambda: True,
        )

        entry = CATALOG["claude-sonnet-4.6"]
        with pytest.raises(ConstructionFailed) as exc_info:
            _construct_from_entry("claude-sonnet-4.6", entry, 1024, "commander")
        assert exc_info.value.reason_code == "budget_paused"

    def test_idle_pause_also_skips_openrouter(self, monkeypatch):
        """The brake represents a TOTAL spend ceiling, so OpenRouter
        is also paused — not just Anthropic.  Previously this gate
        only covered Anthropic, leaving OR spend uncapped during the
        brake window."""
        # Patch the hoisted import in llm_factory's namespace — the
        # canonical pytest pattern is to patch where the function is
        # LOOKED UP, not where it's DEFINED.  The runtime_settings
        # module was imported at module top in llm_factory; patching
        # runtime_settings.get_idle_pause_due_to_budget would NOT
        # affect the captured reference.
        monkeypatch.setattr(
            llm_factory, "get_idle_pause_due_to_budget", lambda: True,
        )

        entry = CATALOG["deepseek-v3.2"]
        with pytest.raises(ConstructionFailed) as exc_info:
            _construct_from_entry("deepseek-v3.2", entry, 1024, "research")
        assert exc_info.value.reason_code == "budget_paused"
        assert "openrouter" in exc_info.value.detail

    def test_idle_pause_does_not_block_local_ollama(self, monkeypatch):
        """Local Ollama costs nothing, so the brake must NOT pause it —
        otherwise during a brake window the operator would lose every
        LLM path including the free one."""
        # Patch the hoisted import in llm_factory's namespace — the
        # canonical pytest pattern is to patch where the function is
        # LOOKED UP, not where it's DEFINED.  The runtime_settings
        # module was imported at module top in llm_factory; patching
        # runtime_settings.get_idle_pause_due_to_budget would NOT
        # affect the captured reference.
        monkeypatch.setattr(
            llm_factory, "get_idle_pause_due_to_budget", lambda: True,
        )
        # Disable local_llm_enabled so construction stops at a known
        # boundary rather than actually attempting Ollama.  This pins
        # that we get to the provider dispatch (i.e. the brake did NOT
        # fire) and fail later with "disabled", not "budget_paused".
        from app.config import get_settings
        original_get_settings = get_settings
        def fake_get_settings():
            s = original_get_settings()
            class _FakeSettings:
                local_llm_enabled = False
                def __getattr__(self, name):
                    return getattr(s, name)
            return _FakeSettings()
        monkeypatch.setattr(llm_factory, "get_settings", fake_get_settings)

        entry = CATALOG["qwen3.5:35b-a3b-q4_K_M"]
        with pytest.raises(ConstructionFailed) as exc_info:
            _construct_from_entry(
                "qwen3.5:35b-a3b-q4_K_M", entry, 1024, "default",
            )
        # The brake didn't fire for Ollama — the next gate did
        # (local_llm_enabled=False → "disabled").
        assert exc_info.value.reason_code == "disabled"

    def test_filter_candidates_skips_dead_models(self, monkeypatch):
        """The catalog's ``_filter_candidates`` consults the health
        cache and drops dead models BEFORE scoring — so a dead model
        can't outrank a live one and the chain walker doesn't waste a
        construction hop on a candidate the cache already rejected.
        """
        from app.llm_catalog import _filter_candidates

        # Mark the premium survivor dead.  Its health key is
        # (entry.provider, derived_id(entry, "native_anthropic")) —
        # claude-sonnet-4.6 routes via OpenRouter post-consolidation.
        llm_factory_probe.mark_dead(
            "openrouter", "anthropic/claude-sonnet-4.6", "test 404",
        )

        # Balanced mode admits every tier; without the health filter
        # claude-sonnet-4.6 would be in the candidate set.
        cands = _filter_candidates(
            mode="balanced",
            tier_floor="local",
            needs_multimodal=False,
            prefer_local=False,
            needs_tools=False,
            skip_dead=True,
        )
        assert "claude-sonnet-4.6" not in cands, (
            "Dead Anthropic model must be filtered out of the candidate "
            "pool before scoring"
        )

        # Sanity: with skip_dead=False (opt-out path), claude-sonnet-4.6
        # comes back — confirms the filter is what's removing it.
        cands_no_skip = _filter_candidates(
            mode="balanced",
            tier_floor="local",
            needs_multimodal=False,
            prefer_local=False,
            needs_tools=False,
            skip_dead=False,
        )
        assert "claude-sonnet-4.6" in cands_no_skip

    def test_cost_advisor_end_to_end_run_stages_proposals(self, monkeypatch):
        """Pin the integration: analyzer produces observations →
        proposer decides → proposal_bridge.stage gets called.  A
        regression in the wiring between analyzer and proposer
        would silently make ``run()`` a no-op.
        """
        from app.llm_cost_advisor import analyzer, proposer
        from app.llm_cost_advisor.analyzer import (
            DailySpend, ProviderObservation, RoleObservation,
        )

        # Stub the analyser to return one CAP-HIT observation +
        # one ROLE-OVERSPEND observation.
        anth_obs = ProviderObservation(
            provider="anthropic",
            cap_usd=5.0,
            days=tuple(
                DailySpend(
                    provider="anthropic", day=f"2026-05-{18 + i:02d}",
                    spend_usd=5.10 if i in (0, 2, 4) else 2.0,
                    n_calls=10,
                ) for i in range(7)
            ),
            max_day_spend_usd=5.10,
            mean_day_spend_usd=3.0,
            n_days_at_or_over_cap=3,
            n_days_below_25pct_of_cap=0,
        )
        role_obs = RoleObservation(
            role="research",
            spend_usd_24h=200.0,  # 4× the $2/h × 24h baseline
            profile_budget_usd=0.50,
            profile_expected_hourly_usd=2.0,
        )
        monkeypatch.setattr(
            analyzer, "analyze_provider_caps", lambda window_days=7: [anth_obs],
        )
        monkeypatch.setattr(
            analyzer, "analyze_role_budgets", lambda hours=24.0: [role_obs],
        )
        # Same monkeypatch in the proposer module's namespace (imports
        # are by-value).
        monkeypatch.setattr(
            proposer, "analyze_provider_caps", lambda window_days=7: [anth_obs],
        )
        monkeypatch.setattr(
            proposer, "analyze_role_budgets", lambda hours=24.0: [role_obs],
        )

        # Capture proposal_bridge.stage calls.
        staged_calls = []
        from app.proposal_bridge import store as pb_store

        def fake_stage(*, source, signature, title, body_markdown,
                       target_path, cooldown_days=7,
                       coding_session_spec=None):
            staged_calls.append({
                "source": source,
                "signature": signature,
                "title": title,
            })
            class _S: pass
            return (_S(), True)

        monkeypatch.setattr(pb_store, "stage", fake_stage)

        # Wipe the 24h cadence guard so the run() actually executes
        # (it would otherwise short-circuit if it ran today already).
        # Tests that exercise the cadence directly use a tmp path
        # via monkeypatch; this one just resets state.
        import app.llm_cost_advisor as _adv
        _adv._reset_cadence_for_tests()

        # Stage 1: direct proposer.run() returns the staged list.
        result = proposer.run()
        # One provider proposal + one role proposal.
        assert len(result) == 2
        assert len(staged_calls) == 2
        # All staged through the canonical source identifier.
        assert all(c["source"] == "llm_cost_advisor" for c in staged_calls)
        # Provider signature followed by role signature (order from run()).
        sigs = [c["signature"] for c in staged_calls]
        assert "anthropic__raise" in sigs
        assert any(s.startswith("role__research__") for s in sigs)

    def test_cost_advisor_cadence_short_circuits_within_24h(self, monkeypatch, tmp_path):
        """LIGHT-pass cadence guard — once the advisor runs, subsequent
        invocations within 24h return [] without touching SQL or
        proposal_bridge.  Catches a regression where the per-pass
        cadence stops being enforced and the analyser fires on every
        LIGHT pass."""
        import app.llm_cost_advisor as _adv

        # Point cadence file at tmp dir so the test doesn't touch
        # the real workspace path.
        cadence_path = tmp_path / "last_run.txt"
        monkeypatch.setattr(_adv, "_CADENCE_PATH", cadence_path)

        # Stub the unguarded run to track invocations.
        invocations = []
        def fake_unguarded():
            invocations.append("ran")
            return [{"provider": "anthropic", "action": "raise"}]
        monkeypatch.setattr(_adv, "_run_unguarded", fake_unguarded)

        # First call: cadence file doesn't exist → run executes.
        result1 = _adv.run()
        assert invocations == ["ran"]
        assert len(result1) == 1
        assert cadence_path.exists()

        # Second call within 24h: cadence guard short-circuits.
        result2 = _adv.run()
        assert invocations == ["ran"]   # unchanged — no re-run
        assert result2 == []

        # Aged cadence file (>24h ago) → next call re-runs.
        import time
        cadence_path.write_text(f"{time.time() - 25 * 3600}\n")
        result3 = _adv.run()
        assert len(invocations) == 2
        assert len(result3) == 1

    def test_cost_advisor_role_proposes_raise_on_overspend(self):
        """Per-role: 24h spend > 4× baseline → propose raising the
        expected_hourly profile field."""
        from app.llm_cost_advisor.analyzer import RoleObservation
        from app.llm_cost_advisor.proposer import _decide_role_adjustment

        obs = RoleObservation(
            role="research",
            spend_usd_24h=200.0,  # 4.16× the $2/h × 24h = $48 baseline
            profile_budget_usd=0.50,
            profile_expected_hourly_usd=2.0,
        )
        decision = _decide_role_adjustment(obs)
        assert decision is not None
        assert decision["action"] == "raise_expected_hourly"
        # Doubled: 2.0 → 4.0
        assert decision["new_expected_hourly_usd"] == pytest.approx(4.0)

    def test_cost_advisor_role_proposes_lower_on_under_use(self):
        """Per-role: 24h spend < 0.1× baseline → propose lowering."""
        from app.llm_cost_advisor.analyzer import RoleObservation
        from app.llm_cost_advisor.proposer import _decide_role_adjustment

        obs = RoleObservation(
            role="coding",
            spend_usd_24h=1.0,    # 1/48 = 2% of baseline
            profile_budget_usd=0.50,
            profile_expected_hourly_usd=2.0,
        )
        decision = _decide_role_adjustment(obs)
        assert decision is not None
        assert decision["action"] == "lower_expected_hourly"
        # Halved: 2.0 → 1.0
        assert decision["new_expected_hourly_usd"] == pytest.approx(1.0)

    def test_cost_ledger_end_to_end_with_real_sqlite(self, tmp_path, monkeypatch):
        """End-to-end pin against a REAL SQLite database — exercises
        the production data path that the stubbed unit tests miss.

        The §9 dormancy bug went undetected for months because every
        test stubbed the reader.  This test creates an actual SQLite
        file with the live ``token_usage`` schema, inserts realistic
        rows, and asserts the ledger sees them.  A regression where
        someone renames the table, changes a column, or breaks the
        provider classifier would fail this test.
        """
        import sqlite3
        from datetime import datetime, timezone

        # Build the live schema in a tmp DB.
        db_path = tmp_path / "llm_benchmarks.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE token_usage (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                model             TEXT NOT NULL,
                prompt_tokens     INTEGER NOT NULL,
                completion_tokens INTEGER NOT NULL,
                total_tokens      INTEGER NOT NULL,
                cost_usd          REAL NOT NULL DEFAULT 0.0,
                ts                TEXT NOT NULL,
                project_id        TEXT,
                agent_role        TEXT
            )
        """)
        now_iso = datetime.now(timezone.utc).isoformat()
        # Three rows: Anthropic Sonnet, OpenRouter DeepSeek, Ollama Qwen
        # — covering all three providers + an agent_role for the
        # per-role aggregation.
        conn.executemany(
            "INSERT INTO token_usage "
            "(model, prompt_tokens, completion_tokens, total_tokens, "
            " cost_usd, ts, project_id, agent_role) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("anthropic/claude-sonnet-4-6", 1000, 500, 1500,
                 0.005, now_iso, None, "commander"),
                ("openrouter/deepseek/deepseek-chat", 2000, 1000, 3000,
                 0.001, now_iso, None, "research"),
                ("ollama_chat/qwen3.5:35b-a3b-q4_K_M", 500, 500, 1000,
                 0.0, now_iso, None, "writing"),
                # Another Anthropic row to verify aggregation.
                ("claude-sonnet-4-6", 500, 200, 700,
                 0.003, now_iso, None, "commander"),
            ],
        )
        conn.commit()
        conn.close()

        # Point the ledger at the tmp DB.
        from app import llm_cost_ledger
        monkeypatch.setattr(llm_cost_ledger, "_DB_PATH", db_path)
        llm_cost_ledger._invalidate_for_tests()

        # Anthropic spend = 0.005 + 0.003 = 0.008
        assert llm_cost_ledger.spend_for_provider("anthropic", hours=24.0) == pytest.approx(0.008)
        # OpenRouter spend = 0.001
        assert llm_cost_ledger.spend_for_provider("openrouter", hours=24.0) == pytest.approx(0.001)
        # Ollama row had cost_usd=0.0, EXCLUDED by the "cost_usd > 0" filter
        assert llm_cost_ledger.spend_for_provider("ollama", hours=24.0) == 0.0

        # Per-role:
        # commander = 0.005 + 0.003 = 0.008
        # research = 0.001
        # writing has zero cost rows, so absent from spend_by_role
        llm_cost_ledger._invalidate_for_tests()  # reset cache
        by_role = llm_cost_ledger.spend_by_role(hours=24.0)
        assert by_role.get("commander", 0.0) == pytest.approx(0.008)
        assert by_role.get("research", 0.0) == pytest.approx(0.001)
        # "writing" had zero cost — excluded by the cost_usd > 0 filter,
        # so it shouldn't appear in the per-role spend dict at all.
        assert "writing" not in by_role or by_role["writing"] == 0.0

        # Daily-spend-for-advisor returns the same Anthropic total
        # under today's UTC-day bucket.
        llm_cost_ledger._invalidate_for_tests()
        daily = llm_cost_ledger.daily_spend_by_provider_for_advisor(7)
        anth_today = next(
            (d for d in daily["anthropic"]
             if d["day"] == datetime.now(timezone.utc).date().isoformat()),
            None,
        )
        assert anth_today is not None
        assert anth_today["spend_usd"] == pytest.approx(0.008)
        assert anth_today["n_calls"] == 2

    def test_cost_advisor_role_lower_floor_suppresses_sporadic(self):
        """Sporadic legitimate roles (run once a day) would otherwise
        always look under-pace because expected_hourly assumes 24/7
        usage.  Min-activity floor suppresses the LOWER proposal
        when 24h spend is below $0.10.
        """
        from app.llm_cost_advisor.analyzer import RoleObservation
        from app.llm_cost_advisor.proposer import _decide_role_adjustment

        # self_improve runs once a quarter — 24h spend $0.05 against
        # baseline $1/h × 24 = $24/day.  ratio = 0.002 → would
        # normally LOWER, but the floor suppresses.
        obs = RoleObservation(
            role="self_improve",
            spend_usd_24h=0.05,
            profile_budget_usd=0.50,
            profile_expected_hourly_usd=1.0,
        )
        assert _decide_role_adjustment(obs) is None, (
            "Sporadic legitimately-low-traffic roles must not trigger "
            "LOWER — the baseline assumes continuous usage they don't "
            "have"
        )

    def test_cost_advisor_role_no_proposal_when_within_band(self):
        """Per-role: usage within [0.1, 4]× baseline → no proposal.
        Avoids advisor spam for roles that are right-sized."""
        from app.llm_cost_advisor.analyzer import RoleObservation
        from app.llm_cost_advisor.proposer import _decide_role_adjustment

        obs = RoleObservation(
            role="commander",
            spend_usd_24h=5.0,  # ~1× the $0.20/h × 24h = $4.80 baseline
            profile_budget_usd=0.05,
            profile_expected_hourly_usd=0.20,
        )
        assert _decide_role_adjustment(obs) is None

    def test_cost_advisor_thresholds_tunable_via_runtime_settings(self, monkeypatch):
        """Thresholds are read from runtime_settings, not hardcoded.
        Set raise_n_days to 5 and a 3-of-7 observation should NOT trigger."""
        from app.llm_cost_advisor.analyzer import (
            DailySpend, ProviderObservation,
        )
        from app.llm_cost_advisor.proposer import _decide_adjustment
        from app import runtime_settings

        # Tighten the raise trigger from 3 to 5 of 7 days.
        monkeypatch.setattr(
            runtime_settings, "get_cost_advisor_raise_n_days",
            lambda: 5,
        )
        days = tuple(
            DailySpend(
                provider="anthropic", day=f"2026-05-{18+i:02d}",
                spend_usd=5.10 if i in (0, 2, 4) else 2.0,
                n_calls=10,
            )
            for i in range(7)
        )
        obs = ProviderObservation(
            provider="anthropic",
            cap_usd=5.0,
            days=days,
            max_day_spend_usd=5.10,
            mean_day_spend_usd=3.0,
            n_days_at_or_over_cap=3,   # below the tightened threshold of 5
            n_days_below_25pct_of_cap=0,
        )
        # Should NOT trigger — operator-tightened threshold pins to 5+.
        assert _decide_adjustment(obs) is None

    def test_cost_advisor_proposes_raise_when_cap_hit_repeatedly(self):
        """When the cap was hit on ≥3 of 7 days, the advisor proposes
        a 25% raise.  Pure-function test of the decision rule — no
        proposal-bridge involvement.
        """
        from app.llm_cost_advisor.analyzer import (
            DailySpend, ProviderObservation,
        )
        from app.llm_cost_advisor.proposer import _decide_adjustment

        days = tuple(
            DailySpend(
                provider="anthropic", day=f"2026-05-{18+i:02d}",
                spend_usd=5.10 if i in (0, 2, 4) else 2.0,
                n_calls=10,
            )
            for i in range(7)
        )
        obs = ProviderObservation(
            provider="anthropic",
            cap_usd=5.0,
            days=days,
            max_day_spend_usd=5.10,
            mean_day_spend_usd=3.0,
            n_days_at_or_over_cap=3,
            n_days_below_25pct_of_cap=0,
        )
        decision = _decide_adjustment(obs)
        assert decision is not None
        assert decision["action"] == "raise"
        # 25% raise: 5.0 * 1.25 = 6.25
        assert decision["new_cap_usd"] == pytest.approx(6.25)

    def test_cost_advisor_proposes_lower_when_under_used(self):
        """Cap > 0 but < 25% utilised on ≥6 of 7 days → propose 50% lower
        (provided total 7d spend clears the min-activity floor).
        """
        from app.llm_cost_advisor.analyzer import (
            DailySpend, ProviderObservation,
        )
        from app.llm_cost_advisor.proposer import _decide_adjustment

        days = tuple(
            DailySpend(
                provider="openrouter", day=f"2026-05-{18+i:02d}",
                spend_usd=1.0, n_calls=5,
            )
            for i in range(7)
        )
        obs = ProviderObservation(
            provider="openrouter",
            cap_usd=20.0,            # 1.0 / 20.0 = 5% < 25%
            days=days,
            max_day_spend_usd=1.0,
            mean_day_spend_usd=1.0,
            n_days_at_or_over_cap=0,
            n_days_below_25pct_of_cap=7,  # all 7 days under 25%
        )
        decision = _decide_adjustment(obs)
        assert decision is not None
        assert decision["action"] == "lower"
        # 50% lower: 20.0 * 0.5 = 10.0
        assert decision["new_cap_usd"] == pytest.approx(10.0)

    def test_cost_advisor_lower_floor_suppresses_low_traffic(self):
        """Min-activity floor: LOWER must NOT fire when 7-day total is
        below $0.50 — that's migration-window or genuinely-idle provider
        signal, not "cap is too tight".  Catches the post-§9 dormancy-
        recovery regression class.
        """
        from app.llm_cost_advisor.analyzer import (
            DailySpend, ProviderObservation,
        )
        from app.llm_cost_advisor.proposer import _decide_adjustment

        # 7 days × $0.05 = $0.35 total — below the $0.50 floor.
        days = tuple(
            DailySpend(
                provider="openrouter", day=f"2026-05-{18+i:02d}",
                spend_usd=0.05, n_calls=1,
            )
            for i in range(7)
        )
        obs = ProviderObservation(
            provider="openrouter",
            cap_usd=20.0,
            days=days,
            max_day_spend_usd=0.05,
            mean_day_spend_usd=0.05,
            n_days_at_or_over_cap=0,
            n_days_below_25pct_of_cap=7,  # would normally trigger LOWER
        )
        assert _decide_adjustment(obs) is None, (
            "Min-activity floor must suppress LOWER when 7d spend "
            "is below $0.50 — operator hasn't generated enough signal"
        )

    def test_cost_advisor_no_proposal_when_well_calibrated(self):
        """Cap hit 0-2× and not chronically under-used → no proposal.
        Avoids advisor spam for caps that are already right-sized.
        """
        from app.llm_cost_advisor.analyzer import (
            DailySpend, ProviderObservation,
        )
        from app.llm_cost_advisor.proposer import _decide_adjustment

        days = tuple(
            DailySpend(
                provider="anthropic", day=f"2026-05-{18+i:02d}",
                spend_usd=3.0, n_calls=10,
            )
            for i in range(7)
        )
        obs = ProviderObservation(
            provider="anthropic",
            cap_usd=5.0,
            days=days,
            max_day_spend_usd=3.0,    # 60% utilisation — healthy
            mean_day_spend_usd=3.0,
            n_days_at_or_over_cap=0,
            n_days_below_25pct_of_cap=0,
        )
        assert _decide_adjustment(obs) is None

    def test_cost_advisor_proposes_set_when_no_cap_and_real_spend(self):
        """No cap configured + non-trivial spend → propose setting one."""
        from app.llm_cost_advisor.analyzer import (
            DailySpend, ProviderObservation,
        )
        from app.llm_cost_advisor.proposer import _decide_adjustment

        days = tuple(
            DailySpend(
                provider="openrouter", day=f"2026-05-{18+i:02d}",
                spend_usd=3.0, n_calls=15,
            )
            for i in range(7)
        )
        obs = ProviderObservation(
            provider="openrouter",
            cap_usd=None,
            days=days,
            max_day_spend_usd=3.0,
            mean_day_spend_usd=3.0,
            n_days_at_or_over_cap=0,
            n_days_below_25pct_of_cap=0,
        )
        decision = _decide_adjustment(obs)
        assert decision is not None
        assert decision["action"] == "set"
        # 2× max-day: 3.0 * 2.0 = 6.0
        assert decision["new_cap_usd"] == pytest.approx(6.0)

    def test_adaptive_budget_factor_tightens_for_overspend(self, monkeypatch):
        """When a role's rolling-1h spend exceeds the expected hourly
        pace, ``adaptive_budget_factor`` returns < 1.0 so the next
        call's budget tightens — biasing the selector's Pareto demote
        toward a cheaper alternative.
        """
        from app import llm_role_spend

        # Stub the audit-log read so the test is hermetic.
        def fake_spend(role, hours=1.0):
            # ``commander`` expected $0.20/h; we report $1.20/h = 6×
            return 1.20 if role == "commander" else 0.0
        monkeypatch.setattr(llm_role_spend, "spent_in_window", fake_spend)

        # Commander is 6× over pace → aggressive tightening (0.25)
        factor = llm_role_spend.adaptive_budget_factor("commander")
        assert factor == 0.25

        # Vetting is at zero spend → no tightening (1.0)
        assert llm_role_spend.adaptive_budget_factor("vetting") == 1.0

    def test_adaptive_factor_flows_into_resolved_budget(self, monkeypatch):
        """``_resolved_budget_usd(role)`` must multiply the per-role
        base by the adaptive factor.  A 0.5 factor halves the budget;
        a 1.0 factor leaves it unchanged.
        """
        from app import llm_role_spend
        monkeypatch.setattr(
            llm_role_spend, "adaptive_budget_factor", lambda role: 0.5,
        )
        base = llm_role_spend._ROLE_PROFILES["commander"].budget_usd
        resolved = llm_factory._resolved_budget_usd("commander")
        assert resolved == pytest.approx(base * 0.5)

    def test_adaptive_factor_failure_open(self, monkeypatch):
        """If the role-spend module raises, ``_resolved_budget_usd``
        falls back to the base budget — broken telemetry must NEVER
        starve calls."""
        from app import llm_role_spend
        def boom(role):
            raise RuntimeError("audit log corrupted")
        monkeypatch.setattr(llm_role_spend, "adaptive_budget_factor", boom)
        base = llm_role_spend._ROLE_PROFILES["commander"].budget_usd
        resolved = llm_factory._resolved_budget_usd("commander")
        assert resolved == base  # no tightening on read error

    def test_budget_aware_completion_fires_per_call_pre_check(self, monkeypatch):
        """BudgetAwareCompletion subclasses crewai.LLM and injects a
        per-call pre_check.  When the budget module's pre_check raises
        its typed cap-exceeded exception, the wrapper propagates —
        symmetric with the Anthropic CreditAware wrapper's per-call
        cap behaviour.
        """
        try:
            from app.llms.budget_aware import BudgetAwareCompletion
        except Exception:
            pytest.skip("crewai not available in this test env")

        from app import llm_openrouter_budget

        # Fake budget module that always refuses.
        class _RefusingBudget:
            def pre_check(self, estimated_cost_usd=0.0):
                raise llm_openrouter_budget.OpenRouterDailyCapExceeded(
                    today_spent_usd=10.0, daily_cap_usd=5.0, estimated_cost_usd=0.0,
                )

        # Construct via model_construct to avoid the heavy LLM init.
        try:
            llm = BudgetAwareCompletion.model_construct(
                model="openrouter/deepseek/deepseek-chat",
                max_tokens=256,
            )
        except Exception:
            pytest.skip("BudgetAwareCompletion construction shape not testable in env")

        llm.set_budget_module(_RefusingBudget())
        # The pre_check raises — wrapper must propagate.
        with pytest.raises(llm_openrouter_budget.OpenRouterDailyCapExceeded):
            llm._run_pre_check()

    def test_openrouter_daily_cap_refuses_at_construction(self, monkeypatch):
        """Sibling to the Anthropic cap.  When the OpenRouter daily
        cap is engaged and ``_construct_from_entry`` is called for an
        OpenRouter entry, it must raise ``ConstructionFailed
        ("budget_paused", …)`` so the chain walker falls through.
        """
        # Stub the cap to fire immediately by mocking pre_check.
        from app import llm_openrouter_budget
        def fake_pre_check(estimated_cost_usd=0.0):
            raise llm_openrouter_budget.OpenRouterDailyCapExceeded(
                today_spent_usd=10.0, daily_cap_usd=5.0, estimated_cost_usd=0.0,
            )
        monkeypatch.setattr(
            llm_openrouter_budget, "pre_check", fake_pre_check,
        )

        entry = CATALOG["deepseek-v3.2"]
        with pytest.raises(ConstructionFailed) as exc_info:
            _construct_from_entry("deepseek-v3.2", entry, 1024, "research")
        # Translated to budget_paused — same reason code as the
        # idle_pause_due_to_budget brake so the chain walker treats
        # it uniformly.
        assert exc_info.value.reason_code == "budget_paused"

    def test_budget_fallback_sentinel_for_role_named_default(self):
        """A role literally named ``default`` should return the
        fallback sentinel, not collide with a magic dict key.  The
        §26 refactor replaced ``_DEFAULT_BUDGET_USD_BY_ROLE["default"]``
        with ``_BUDGET_FALLBACK_USD``; this pins that contract.
        """
        from app import llm_role_spend
        budget = llm_factory._resolved_budget_usd("default")
        # Returns the fallback profile's budget — distinct from any
        # per-role row, returned by the shared sentinel structure.
        assert budget == llm_role_spend._FALLBACK_PROFILE.budget_usd
        assert budget != llm_role_spend._ROLE_PROFILES["commander"].budget_usd
        # An unrelated unknown role also gets the fallback.
        assert llm_factory._resolved_budget_usd("nonexistent-role") == budget

    def test_orchestrator_catches_anthropic_daily_cap_exceeded(self):
        """The orchestrator's typed-catch arm exists for both
        Anthropic and OpenRouter cap exceptions via the
        ``CapExceededError`` base class.  Pin that:
          * the imports are at module top (not inline in the except)
          * the arm produces a provider-named, budget-specific user
            reply (not "trouble understanding")
        Failure means the §19/§43 catch arm regressed.
        """
        import app.agents.commander.orchestrator as orch
        # Module-top imports — accessing the attributes must NOT raise.
        assert hasattr(orch, "AnthropicDailyCapExceeded")
        assert hasattr(orch, "CapExceededError")
        assert hasattr(orch, "NoWorkingModelAvailable")

        # The arm catches the base class so both Anthropic and OR
        # cap exceptions surface as a budget-specific user reply.
        import inspect
        src = inspect.getsource(orch)
        assert "isinstance(exc, CapExceededError)" in src, (
            "Orchestrator must catch CapExceededError (the base class) "
            "so OR cap exceptions are surfaced like Anthropic ones.  "
            "If this fails, OR cap-exceeded would fall through to the "
            "generic 'trouble understanding' arm — same bug class the "
            "§19 catch was supposed to eliminate."
        )
        assert "budget is exhausted" in src


# ── _construct_from_entry contract ────────────────────────────────────

class TestConstructFromEntry:
    """Per-candidate construction must fail with typed reason codes."""

    def test_shape_invalid_raises_with_reason(self):
        bad_entry = {
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",  # missing prefix
        }
        with pytest.raises(ConstructionFailed) as exc_info:
            _construct_from_entry("bad", bad_entry, 1024, "commander")
        assert exc_info.value.reason_code == "shape_invalid"

    def test_marked_dead_raises_with_reason(self):
        entry = _BOOTSTRAP_CATALOG["claude-sonnet-4.6"]
        # Mark its health key dead (entry.provider + native_anthropic
        # route id) — claude-sonnet-4.6 routes via OpenRouter now.
        llm_factory_probe.mark_dead(
            "openrouter", "anthropic/claude-sonnet-4.6", "test setup",
        )
        with pytest.raises(ConstructionFailed) as exc_info:
            _construct_from_entry(
                "claude-sonnet-4.6", entry, 1024, "commander",
            )
        assert exc_info.value.reason_code == "marked_dead"

    def test_unknown_provider_raises(self):
        weird_entry = {
            "provider": "alien-cloud-9000",
            "model_id": "alien-cloud-9000/model-x",
        }
        # validate_entry passes (we don't enforce a closed provider
        # list there), but the dispatch must catch it.
        # Add the prefix to ALLOW the entry through validation so the
        # provider check is the failing layer.
        with pytest.raises(ConstructionFailed) as exc_info:
            _construct_from_entry("weird", weird_entry, 1024, "commander")
        # Could be shape_invalid OR unknown_provider depending on the
        # validator — both are correct refusal modes.
        assert exc_info.value.reason_code in ("shape_invalid", "unknown_provider")


# ── _walk_chain contract ──────────────────────────────────────────────

class TestWalkChain:
    """The walker iterates candidates and returns the first that
    constructs, or raises NoWorkingModelAvailable."""

    def test_empty_chain_raises(self):
        with pytest.raises(NoWorkingModelAvailable) as exc_info:
            _walk_chain([], max_tokens=1024, role="commander")
        assert exc_info.value.role == "commander"
        assert exc_info.value.attempts == []

    def test_all_not_in_catalog_raises(self):
        with pytest.raises(NoWorkingModelAvailable) as exc_info:
            _walk_chain(
                ["fake-model-1", "fake-model-2"],
                max_tokens=1024, role="commander",
            )
        assert len(exc_info.value.attempts) == 2
        for name, failure in exc_info.value.attempts:
            assert failure.reason_code == "not_in_catalog"

    def test_skips_marked_dead_returns_next(self, monkeypatch):
        # Mark the premium survivor dead; walker should skip and try
        # deepseek-v3.2.  We monkeypatch the actual builders so the
        # test doesn't make network calls.
        llm_factory_probe.mark_dead(
            "anthropic", "claude-sonnet-4-6", "test mark",
        )

        sentinel = object()

        def fake_try_api(name, entry, max_tokens, role, phase=None):
            # Only respond for deepseek-v3.2 — fail for others so the
            # walker has a known stopping point.
            if name == "deepseek-v3.2":
                return sentinel
            return None

        # Mock the API-key getters directly so the test is robust
        # against other tests in the suite that mock get_settings().
        monkeypatch.setattr(
            llm_factory, "get_openrouter_api_key", lambda: "test-key",
        )
        monkeypatch.setattr(llm_factory, "_try_api", fake_try_api)

        result = _walk_chain(
            ["claude-sonnet-4.6", "deepseek-v3.2"],
            max_tokens=1024, role="commander",
        )
        assert result is sentinel

    def test_dedups_repeated_candidates(self, monkeypatch):
        sentinel = object()

        def fake_try_api(name, entry, max_tokens, role, phase=None):
            return sentinel

        monkeypatch.setattr(
            llm_factory, "get_openrouter_api_key", lambda: "test-key",
        )
        monkeypatch.setattr(llm_factory, "_try_api", fake_try_api)

        # Walker dedups, so listing the same name twice is harmless.
        result = _walk_chain(
            ["deepseek-v3.2", "deepseek-v3.2"],
            max_tokens=1024, role="commander",
        )
        assert result is sentinel


