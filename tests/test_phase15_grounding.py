"""Phase 15 — Factual Grounding & Correction Memory tests.

Covers all six grounding components + the chat bridge + the
end-to-end Tallink-share-price regression scenario that motivated
this phase.

Sections:
  A. Claim extraction
  B. Source registry persistence
  C. BeliefAdapter (in-memory + Phase 2)
  D. Per-claim decision logic
  E. Response rewriting (3 paths: ALLOW / ESCALATE / BLOCK)
  F. Correction capture (detection + synchronous persist)
  G. Pipeline orchestration + feature flag
  H. Chat bridge (defensive wrappers)
  I. Tallink-scenario regression — THE bug this phase fixes
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.subia.grounding import (
    BeliefAdapter, ClaimKind, CorrectionCapture, DetectedCorrection,
    EvidenceCheck, FactualClaim, GroundingDecision, GroundingPipeline,
    GroundingPipelineConfig, GroundingResult, GroundingVerdict,
    InMemoryBeliefAdapter, RewriterResult, SourceRegistry,
    decide_for_claim, extract_claims, rewrite_response,
)


# ─────────────────────────────────────────────────────────────────────
# A. Claim extraction
# ─────────────────────────────────────────────────────────────────────

class TestClaimExtraction:
    def test_no_claims_in_chitchat(self):
        assert extract_claims("Hi! Happy to help. What would you like?") == []

    def test_currency_claim_detected(self):
        cs = extract_claims("Tallink share price is €0.595.")
        assert cs and cs[0].kind == ClaimKind.NUMERIC_PRICE
        assert "0.595" in cs[0].normalized_value

    def test_high_stakes_requires_date_or_source(self):
        # Bare currency: low-stakes (could be a hypothetical)
        cs = extract_claims("This stock could be worth €0.50.")
        assert cs and not cs[0].is_high_stakes()

    def test_currency_plus_date_is_high_stakes(self):
        cs = extract_claims("On April 14, 2022, Tallink share price was €0.595.")
        assert cs and cs[0].is_high_stakes()
        assert "April 14, 2022" in cs[0].attributed_date

    def test_currency_plus_source_is_high_stakes(self):
        cs = extract_claims("Tallink share price is €0.623 (Source: Nasdaq Baltic).")
        assert cs and cs[0].is_high_stakes()
        assert "Nasdaq Baltic" in cs[0].attributed_source

    def test_topic_hint_inferred_from_context(self):
        cs = extract_claims("Tallink share price was €0.60 on April 14, 2022.")
        assert cs and cs[0].topic_hint == "share_price"

    def test_multiple_currencies(self):
        cs = extract_claims("On 2024-12-31, A was $50 and B was €20.")
        assert len(cs) == 2

    def test_eur_suffix_form(self):
        cs = extract_claims("Tallink share price is 0.623 EUR.")
        assert cs and "0.623" in cs[0].normalized_value


# ─────────────────────────────────────────────────────────────────────
# B. Source registry
# ─────────────────────────────────────────────────────────────────────

class TestSourceRegistry:
    def test_register_and_get(self, tmp_path):
        reg = SourceRegistry(path=tmp_path / "reg.json")
        reg.register("share_price", "default",
                     "https://nasdaqbaltic.com/", learned_from="user_correction")
        rs = reg.get("share_price")
        assert rs is not None and rs.url == "https://nasdaqbaltic.com/"
        assert rs.learned_from == "user_correction"

    def test_persists_across_instances(self, tmp_path):
        path = tmp_path / "reg.json"
        reg1 = SourceRegistry(path=path)
        reg1.register("share_price", "TAL1T",
                      "https://nasdaqbaltic.com/?id=TAL1T")
        reg2 = SourceRegistry(path=path)   # fresh instance, same file
        rs = reg2.get("share_price", "TAL1T")
        assert rs is not None and "TAL1T" in rs.url

    def test_topic_default_fallback(self, tmp_path):
        reg = SourceRegistry(path=tmp_path / "reg.json")
        reg.register("share_price", "default", "https://nasdaqbaltic.com/")
        # Asking for a specific key falls back to default
        rs = reg.get("share_price", "ANY_TICKER")
        assert rs is not None

    def test_re_register_overwrites_url(self, tmp_path):
        reg = SourceRegistry(path=tmp_path / "reg.json")
        reg.register("share_price", "default", "https://wrong.example/")
        reg.register("share_price", "default", "https://nasdaqbaltic.com/")
        assert reg.get("share_price").url == "https://nasdaqbaltic.com/"


# ─────────────────────────────────────────────────────────────────────
# C. BeliefAdapter
# ─────────────────────────────────────────────────────────────────────

class TestBeliefAdapter:
    def test_in_memory_upsert_and_find(self):
        a = InMemoryBeliefAdapter()
        b = a.upsert("share_price::april_14_2022", "0.595 EUR",
                     [{"source": "user_correction"}], confidence=0.9)
        found = a.find("share_price::april_14_2022")
        assert found is not None and found.value == "0.595 EUR"
        assert found.is_verified()

    def test_supersede_others_marks_old_beliefs(self):
        a = InMemoryBeliefAdapter()
        b1 = a.upsert("k", "0.60", [{"source": "guess"}], confidence=0.5)
        b2 = a.upsert("k", "0.595", [{"source": "user"}], confidence=0.9)
        n = a.supersede_others("k", b2.belief_id, "user_correction")
        assert n == 1
        # Find returns ACTIVE only
        assert a.find("k").belief_id == b2.belief_id


# ─────────────────────────────────────────────────────────────────────
# D. Per-claim decision logic
# ─────────────────────────────────────────────────────────────────────

class TestEvidenceDecision:
    def test_no_belief_escalates(self, tmp_path):
        adapter = InMemoryBeliefAdapter()
        reg = SourceRegistry(path=tmp_path / "reg.json")
        c = extract_claims(
            "Tallink share price was €0.60 on April 14, 2022."
        )[0]
        v = decide_for_claim(c, belief_adapter=adapter, source_registry=reg)
        assert v.decision == GroundingDecision.ESCALATE

    def test_matching_belief_allows(self, tmp_path):
        adapter = InMemoryBeliefAdapter()
        reg = SourceRegistry(path=tmp_path / "reg.json")
        c = extract_claims(
            "Tallink share price was €0.595 on April 14, 2022."
        )[0]
        from app.subia.grounding.evidence import topic_key_for
        adapter.upsert(topic_key_for(c), "0.595",
                       [{"source": "nasdaqbaltic.com"}], confidence=0.95)
        v = decide_for_claim(c, belief_adapter=adapter, source_registry=reg)
        assert v.decision == GroundingDecision.ALLOW

    def test_contradicting_belief_blocks(self, tmp_path):
        adapter = InMemoryBeliefAdapter()
        reg = SourceRegistry(path=tmp_path / "reg.json")
        # Verified belief: 0.595
        c1 = extract_claims(
            "Tallink share price was €0.595 on April 14, 2022."
        )[0]
        from app.subia.grounding.evidence import topic_key_for
        adapter.upsert(topic_key_for(c1), "0.595",
                       [{"source": "nasdaqbaltic.com"}], confidence=0.95)
        # Draft now claims 0.65 for same date → contradiction
        c2 = extract_claims(
            "Tallink share price was €0.65 on April 14, 2022."
        )[0]
        v = decide_for_claim(c2, belief_adapter=adapter, source_registry=reg)
        assert v.decision == GroundingDecision.BLOCK
        assert v.contradicted_belief is not None

    def test_escalate_includes_registered_source(self, tmp_path):
        adapter = InMemoryBeliefAdapter()
        reg = SourceRegistry(path=tmp_path / "reg.json")
        reg.register("share_price", "default", "https://nasdaqbaltic.com/")
        c = extract_claims(
            "Tallink share price was €0.60 on April 14, 2022."
        )[0]
        v = decide_for_claim(c, belief_adapter=adapter, source_registry=reg)
        assert v.suggested_source == "https://nasdaqbaltic.com/"


# ─────────────────────────────────────────────────────────────────────
# E. Response rewriting
# ─────────────────────────────────────────────────────────────────────

class TestRewriter:
    def _check(self, draft, *, beliefs=None, registry=None):
        beliefs = beliefs or InMemoryBeliefAdapter()
        registry = registry or SourceRegistry(path=Path("/tmp/_test_reg.json"))
        verdicts = [
            decide_for_claim(c, belief_adapter=beliefs, source_registry=registry)
            for c in extract_claims(draft) if c.is_high_stakes()
        ]
        return EvidenceCheck(verdicts=verdicts)

    def test_allow_passes_through_unchanged(self, tmp_path):
        beliefs = InMemoryBeliefAdapter()
        from app.subia.grounding.evidence import topic_key_for
        c = extract_claims("Tallink share price was €0.595 on April 14, 2022.")[0]
        beliefs.upsert(topic_key_for(c), "0.595",
                       [{"source": "nasdaqbaltic.com"}], confidence=0.95)
        draft = "Tallink share price was €0.595 on April 14, 2022."
        check = self._check(draft, beliefs=beliefs,
                             registry=SourceRegistry(path=tmp_path / "r.json"))
        out = rewrite_response(draft, check)
        assert out.text == draft and out.decision == GroundingDecision.ALLOW

    def test_escalate_replaces_text_with_honest_question(self, tmp_path):
        reg = SourceRegistry(path=tmp_path / "r.json")
        reg.register("share_price", "default", "https://nasdaqbaltic.com/")
        draft = "Tallink share price was €0.60 on April 14, 2022."
        check = self._check(draft, registry=reg)
        out = rewrite_response(draft, check)
        assert out.decision == GroundingDecision.ESCALATE
        assert "0.60" not in out.text   # the unverified figure removed
        assert "nasdaqbaltic.com" in out.text

    def test_block_refuses_to_send_inconsistent_claim(self, tmp_path):
        beliefs = InMemoryBeliefAdapter()
        from app.subia.grounding.evidence import topic_key_for
        c1 = extract_claims("Tallink share price was €0.595 on April 14, 2022.")[0]
        beliefs.upsert(topic_key_for(c1), "0.595",
                       [{"source": "nasdaqbaltic.com"}], confidence=0.95)
        draft = "Tallink share price was €0.65 on April 14, 2022."
        check = self._check(draft, beliefs=beliefs,
                             registry=SourceRegistry(path=tmp_path / "r.json"))
        out = rewrite_response(draft, check)
        assert out.decision == GroundingDecision.BLOCK
        assert "0.595" in out.text   # rewriter cites the verified value
        assert "0.65" in out.text or "verified" in out.text.lower()

    def test_no_claims_no_rewrite(self, tmp_path):
        check = EvidenceCheck(verdicts=[])
        out = rewrite_response("Hello — happy to help.", check)
        assert out.text == "Hello — happy to help."


# ─────────────────────────────────────────────────────────────────────
# F. Correction capture
# ─────────────────────────────────────────────────────────────────────

class TestCorrectionCapture:
    def _capture(self, tmp_path):
        beliefs = InMemoryBeliefAdapter()
        reg = SourceRegistry(path=tmp_path / "r.json")
        audit_log: list = []
        def audit(finding, loop_count, sources, severity, **kw):
            audit_log.append({"finding": finding, "severity": severity})
        cap = CorrectionCapture(belief_adapter=beliefs,
                                 source_registry=reg,
                                 narrative_audit_fn=audit)
        return cap, beliefs, reg, audit_log

    def test_detect_actually_pattern(self, tmp_path):
        cap, *_ = self._capture(tmp_path)
        c = cap.detect("actually it's €0.595",
                       prior_response="The Tallink share price was €0.65 on April 14, 2022.")
        assert c is not None
        assert "0.595" in c.normalized_value
        assert "April 14, 2022" in c.attributed_date

    def test_detect_i_see_pattern(self, tmp_path):
        cap, *_ = self._capture(tmp_path)
        c = cap.detect("I see that price was €0.595",
                       prior_response="The Tallink share price was €0.60 on April 14, 2022.")
        assert c is not None and "0.595" in c.normalized_value

    def test_detect_source_only_hint(self, tmp_path):
        cap, *_ = self._capture(tmp_path)
        c = cap.detect("You can get this from Tallinn Stock Exchange homepage",
                       prior_response="Tallink share price was €0.60 on April 14, 2022.")
        assert c is not None
        assert c.suggested_source_url == "https://nasdaqbaltic.com/"
        assert c.normalized_value == ""   # source-only

    def test_persist_upserts_belief_and_supersedes(self, tmp_path):
        cap, beliefs, reg, audit_log = self._capture(tmp_path)
        # Pre-existing fabricated belief
        from app.subia.grounding.evidence import topic_key_for
        from app.subia.grounding.claims import FactualClaim, ClaimKind
        fake = FactualClaim(text="0.65", kind=ClaimKind.NUMERIC_PRICE,
                            span=(0, 4), normalized_value="0.65",
                            attributed_date="April 14, 2022",
                            topic_hint="share_price")
        key = topic_key_for(fake)
        beliefs.upsert(key, "0.65", [{"source": "guess"}], confidence=0.4)
        # Now persist the user's correction
        c = cap.detect_and_persist(
            "actually it's €0.595",
            prior_response="Tallink share price was €0.65 on April 14, 2022.",
        )
        assert c is not None
        # Verified belief now exists
        b = beliefs.find(key)
        assert b is not None and "0.595" in b.value
        # Audit appended
        assert audit_log

    def test_persist_registers_source_url(self, tmp_path):
        cap, _, reg, _ = self._capture(tmp_path)
        cap.detect_and_persist(
            "you can get this from Tallinn Stock Exchange homepage",
            prior_response="Tallink share price was €0.60.",
        )
        rs = reg.get("share_price")
        assert rs is not None and "nasdaqbaltic" in rs.url


# ─────────────────────────────────────────────────────────────────────
# G. Pipeline orchestration + feature flag
# ─────────────────────────────────────────────────────────────────────

class TestPipeline:
    def test_disabled_pipeline_passes_through(self, tmp_path):
        p = GroundingPipeline(
            belief_adapter=InMemoryBeliefAdapter(),
            source_registry=SourceRegistry(path=tmp_path / "r.json"),
            config=GroundingPipelineConfig(enabled=False),
        )
        r = p.check_egress("Tallink share price is €0.65 on April 14, 2022.")
        assert r.skipped is True
        assert r.text == "Tallink share price is €0.65 on April 14, 2022."

    def test_enabled_pipeline_escalates_unverified_claim(self, tmp_path):
        reg = SourceRegistry(path=tmp_path / "r.json")
        reg.register("share_price", "default", "https://nasdaqbaltic.com/")
        p = GroundingPipeline(
            belief_adapter=InMemoryBeliefAdapter(),
            source_registry=reg,
            config=GroundingPipelineConfig(enabled=True),
        )
        r = p.check_egress("Tallink share price was €0.65 on April 14, 2022.")
        assert r.decision == GroundingDecision.ESCALATE
        assert r.transformed
        assert "nasdaqbaltic.com" in r.text

    def test_pipeline_observe_persists_correction(self, tmp_path):
        beliefs = InMemoryBeliefAdapter()
        reg = SourceRegistry(path=tmp_path / "r.json")
        p = GroundingPipeline(
            belief_adapter=beliefs, source_registry=reg,
            config=GroundingPipelineConfig(enabled=True),
        )
        c = p.observe_user_message(
            "actually it's €0.595",
            prior_response="Tallink share price was €0.65 on April 14, 2022.",
        )
        assert c is not None
        # Belief now persisted
        from app.subia.grounding.evidence import topic_key_for
        from app.subia.grounding.claims import FactualClaim, ClaimKind
        f = FactualClaim(text="0.595", kind=ClaimKind.NUMERIC_PRICE,
                         span=(0, 5), normalized_value="0.595",
                         attributed_date="April 14, 2022",
                         topic_hint="share_price")
        assert beliefs.find(topic_key_for(f)) is not None

    def test_pipeline_swallows_internal_errors(self, tmp_path):
        class _BadAdapter:
            def find(self, key): raise RuntimeError("boom")
            def upsert(self, *a, **k): raise RuntimeError("boom")
            def retract(self, *a, **k): return False
            def supersede_others(self, *a, **k): return 0
        p = GroundingPipeline(
            belief_adapter=_BadAdapter(),
            source_registry=SourceRegistry(path=tmp_path / "r.json"),
            config=GroundingPipelineConfig(enabled=True),
        )
        # Despite adapter errors, pipeline never crashes the caller
        r = p.check_egress("Tallink share was €0.65 on April 14, 2022.")
        assert r.skipped or r.text  # something always returned


# ─────────────────────────────────────────────────────────────────────
# H. Chat bridge — defensive wrappers
# ─────────────────────────────────────────────────────────────────────

class TestChatBridge:
    def test_ground_response_passes_through_when_disabled(self, tmp_path,
                                                          monkeypatch):
        from app.subia.connections.grounding_chat_bridge import (
            ground_response, reset_pipeline_for_tests,
        )
        monkeypatch.delenv("SUBIA_GROUNDING_ENABLED", raising=False)
        reset_pipeline_for_tests(GroundingPipeline(
            belief_adapter=InMemoryBeliefAdapter(),
            source_registry=SourceRegistry(path=tmp_path / "r.json"),
            config=GroundingPipelineConfig(enabled=False),
        ))
        assert ground_response("anything") == "anything"
        reset_pipeline_for_tests(None)

    def test_ground_response_transforms_when_enabled(self, tmp_path):
        from app.subia.connections.grounding_chat_bridge import (
            ground_response, reset_pipeline_for_tests,
        )
        reg = SourceRegistry(path=tmp_path / "r.json")
        reg.register("share_price", "default", "https://nasdaqbaltic.com/")
        reset_pipeline_for_tests(GroundingPipeline(
            belief_adapter=InMemoryBeliefAdapter(),
            source_registry=reg,
            config=GroundingPipelineConfig(enabled=True),
        ))
        out = ground_response(
            "Tallink share price was €0.65 on April 14, 2022.",
        )
        assert "0.65" not in out
        assert "nasdaqbaltic.com" in out
        reset_pipeline_for_tests(None)

    def test_observe_user_correction_returns_detected(self, tmp_path):
        from app.subia.connections.grounding_chat_bridge import (
            observe_user_correction, reset_pipeline_for_tests,
        )
        reset_pipeline_for_tests(GroundingPipeline(
            belief_adapter=InMemoryBeliefAdapter(),
            source_registry=SourceRegistry(path=tmp_path / "r.json"),
            config=GroundingPipelineConfig(enabled=True),
        ))
        c = observe_user_correction(
            "actually it's €0.595",
            prior_response="Tallink share price was €0.60 on April 14, 2022.",
        )
        assert c is not None and "0.595" in c.normalized_value
        reset_pipeline_for_tests(None)


# ─────────────────────────────────────────────────────────────────────
# I. Tallink-scenario regression — THE bug this phase fixes
# ─────────────────────────────────────────────────────────────────────

class TestTallinkScenarioRegression:
    """Replays the exact six-turn conversation that motivated Phase 15.

    Turn-by-turn, asserts that the new pipeline:
      - escalates fabricated answers BEFORE they reach the user
      - persists user corrections to belief store + source registry
      - blocks subsequent regression to the old fabricated answer
    """

    def _setup(self, tmp_path):
        beliefs = InMemoryBeliefAdapter()
        reg = SourceRegistry(path=tmp_path / "registry.json")
        pipeline = GroundingPipeline(
            belief_adapter=beliefs, source_registry=reg,
            config=GroundingPipelineConfig(enabled=True),
        )
        return pipeline, beliefs, reg

    def test_full_six_turn_replay(self, tmp_path):
        pipeline, beliefs, reg = self._setup(tmp_path)

        # Turn 1: user asks for historical price; bot drafts fabricated €0.60
        draft1 = "The Tallink Grupp AS share price on April 14, 2022, was €0.60."
        r1 = pipeline.check_egress(draft1, user_message="what was tallink share price 4 years ago")
        assert r1.decision == GroundingDecision.ESCALATE, (
            "Turn 1: with no verified belief, fabricated €0.60 must be escalated, not sent"
        )
        assert "0.60" not in r1.text, "the fabricated figure must be stripped"

        # Turn 2: bot drafts a different fabricated number (€0.62) — also escalated
        draft2 = "The historical share price of Tallink Grupp on April 14, 2022 was €0.62 (Source: Nasdaq Baltic)."
        r2 = pipeline.check_egress(draft2)
        assert r2.decision == GroundingDecision.ESCALATE
        assert "0.62" not in r2.text

        # Turn 3: user supplies the correct figure
        c = pipeline.observe_user_message(
            "I see that price was € 0.595",
            prior_response=draft2,
        )
        assert c is not None and "0.595" in c.normalized_value

        # Turn 4: user names the canonical source. Production chat
        # handler keeps a small conversation buffer; we simulate it by
        # passing the topic-bearing prior turn here.
        c2 = pipeline.observe_user_message(
            "You can get this from Tallinn Stock Exchange homepage",
            prior_response=draft2,    # last factual exchange — preserves topic
        )
        assert c2 is not None
        assert reg.get("share_price").url == "https://nasdaqbaltic.com/"

        # Turn 5: same question asked again. Bot drafts fabricated €0.65 again.
        draft5 = "The closest available data is a consensus target price of €0.65 (Source: Stockopedia)."
        r5 = pipeline.check_egress(
            draft5, user_message="what was tallink share price 4 years ago",
        )
        # The verified belief is €0.595 → fabricated €0.65 must be BLOCKED
        # (asymmetric confirmation — verified beats fabricated)
        assert r5.decision == GroundingDecision.BLOCK, (
            "Turn 5: the bot must NOT regress to the fabricated €0.65 after "
            "user corrected with €0.595"
        )
        # The replacement text cites the verified value
        assert "0.595" in r5.text
        # The fabricated figure does not appear unaltered as the answer
        assert "consensus target price of €0.65" not in r5.text

        # Bonus: the source registry now holds the user's source
        rs = reg.get("share_price")
        assert rs is not None and "nasdaqbaltic" in rs.url

    def test_correct_value_passes_through(self, tmp_path):
        """If the bot ever drafts the correct value, it must pass."""
        pipeline, beliefs, reg = self._setup(tmp_path)
        # User correction first
        pipeline.observe_user_message(
            "I see that price was € 0.595",
            prior_response="Tallink share price was €0.60 on April 14, 2022.",
        )
        # Bot now drafts the correct figure — must be ALLOWED
        draft = "Tallink share price was €0.595 on April 14, 2022."
        r = pipeline.check_egress(draft)
        assert r.decision == GroundingDecision.ALLOW
        assert r.text == draft

    def test_chitchat_bypasses_grounding(self, tmp_path):
        """The pipeline must not interfere with non-factual replies."""
        pipeline, *_ = self._setup(tmp_path)
        for chat in (
            "Sure, happy to help!",
            "Got it.",
            "Let me know if you'd like more detail.",
            "👍",
        ):
            r = pipeline.check_egress(chat)
            assert r.text == chat
            assert r.skipped or r.decision == GroundingDecision.ALLOW
