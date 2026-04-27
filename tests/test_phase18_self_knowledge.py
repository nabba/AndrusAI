"""Phase 18 — Self-Knowledge Routing tests.

Generalises Phase 17 (homeostasis routing) to 9 introspection topics.
Each topic has its own gatherer + formatter; the pipeline detects
which topics fire from the user's message and composes per-topic
sections into the system-prompt prefix.

Sections:
  A. Detector — 9 new topics fire on representative phrasings
  B. Per-topic gatherers — defensive when sources missing
  C. Per-topic formatters — produce non-empty sections + Phase 11 framing
  D. Pipeline composition — multi-topic message gets sections concatenated
  E. Phase 17 compatibility — homeostasis routing still works end-to-end
"""
from __future__ import annotations

import pytest

from app.subia.introspection import (
    IntrospectionContext,
    IntrospectionPipeline,
    IntrospectionPipelineConfig,
    classify_introspection,
    is_introspection_question,
)
from app.subia.introspection.detector import IntrospectionTopic as T


# ─────────────────────────────────────────────────────────────────────
# A. Detector — Phase 18 topics
# ─────────────────────────────────────────────────────────────────────

class TestPhase18Detector:
    @pytest.mark.parametrize("msg,expected_topic", [
        ("What do you believe about Tallink share price?",   T.BELIEFS),
        ("What sources do you trust?",                       T.BELIEFS),
        ("Have I corrected you on anything?",                T.BELIEFS),
        ("What hardware are you running on?",                T.TECHNICAL),
        ("How much RAM do you have?",                        T.TECHNICAL),
        ("What models can you run locally?",                 T.TECHNICAL),
        ("How many modules of code do you have?",            T.TECHNICAL),
        ("What have you been doing today?",                  T.HISTORY),
        ("What happened in the last hour?",                  T.HISTORY),
        ("What are you focused on right now?",               T.SCENE),
        ("What's on your mind?",                             T.SCENE),
        ("Have you wondered about anything?",                T.WONDER),
        ("What have you been daydreaming about?",            T.WONDER),
        ("What biases do you have?",                         T.SHADOW),
        ("What are your blind spots?",                       T.SHADOW),
        ("What's your Butlin score?",                        T.SCORECARD),
        ("Show me your consciousness scorecard",             T.SCORECARD),
        ("Have you noticed any drift in yourself?",          T.SCORECARD),
        ("What did you predict for that task?",              T.PREDICTIONS),
        ("How accurate are your predictions on coding?",     T.PREDICTIONS),
        ("What do you think I want?",                        T.SOCIAL_MODEL),
        ("How well do you know me?",                         T.SOCIAL_MODEL),
    ])
    def test_topic_detected(self, msg, expected_topic):
        m = classify_introspection(msg)
        assert m.is_introspection, f"missed: {msg}"
        assert expected_topic in m.topics, (
            f"{msg!r}: expected topic {expected_topic.value}, got "
            f"{[t.value for t in m.topics]}"
        )

    def test_third_party_still_blocked_for_phase18(self):
        # "the user's biases" — third-party SHADOW reference, not introspection
        m = classify_introspection("the user has many biases")
        assert not m.is_introspection


# ─────────────────────────────────────────────────────────────────────
# B. Per-topic gatherers — defensive
# ─────────────────────────────────────────────────────────────────────

class TestPhase18Gatherers:
    """Each gatherer must return a dict (never None / never raise)
    even when the underlying SubIA stores aren't available."""

    def test_beliefs_gather_safe_without_store(self):
        from app.subia.introspection.topics import beliefs
        d = beliefs.gather()
        assert isinstance(d, dict)
        # Always-present keys
        for k in ("active_beliefs", "suspended_beliefs",
                  "registered_sources", "recent_corrections"):
            assert k in d

    def test_technical_gather_returns_dict(self):
        from app.subia.introspection.topics import technical
        d = technical.gather()
        assert isinstance(d, dict)
        assert "host" in d
        assert "resources" in d

    def test_chronicle_gather_returns_dict(self):
        from app.subia.introspection.topics import chronicle
        d = chronicle.gather()
        assert isinstance(d, dict)
        assert "task_summary" in d

    def test_scene_gather_returns_dict(self):
        from app.subia.introspection.topics import scene
        d = scene.gather()
        assert isinstance(d, dict)
        assert "focal_items" in d
        # When kernel not running, kernel_active=False but no exception
        assert "kernel_active" in d

    def test_wonder_shadow_gatherers_return_dict(self):
        from app.subia.introspection.topics import wonder_shadow
        wd = wonder_shadow.gather_wonder()
        sd = wonder_shadow.gather_shadow()
        assert isinstance(wd, dict) and isinstance(sd, dict)

    def test_scorecard_gather_returns_dict(self):
        from app.subia.introspection.topics import scorecard
        d = scorecard.gather()
        assert isinstance(d, dict)
        assert "phase9_exit_criteria" in d

    def test_predictions_gather_returns_dict(self):
        from app.subia.introspection.topics import predictions
        d = predictions.gather()
        assert isinstance(d, dict)
        assert "rolling_accuracy" in d

    def test_social_gather_returns_dict(self):
        from app.subia.introspection.topics import social
        d = social.gather()
        assert isinstance(d, dict)
        assert "entities" in d


# ─────────────────────────────────────────────────────────────────────
# C. Per-topic formatters — non-empty + Phase 11 framing
# ─────────────────────────────────────────────────────────────────────

class TestPhase18Formatters:
    def test_beliefs_format_with_data(self):
        from app.subia.introspection.topics import beliefs
        text = beliefs.format_section({
            "active_beliefs": [{
                "domain": "share_price::april_14_2022", "content": "0.595 EUR",
                "confidence": 0.95, "evidence_count": 1, "status": "ACTIVE",
            }],
            "suspended_beliefs": [],
            "registered_sources": [{
                "topic": "share_price", "key": "default",
                "url": "https://nasdaqbaltic.com/",
                "learned_from": "user_correction", "confidence": 0.9,
            }],
            "recent_corrections": [{
                "at": "2026-04-14T12:00:00Z",
                "finding": "User correction captured: topic=share_price value=0.595",
            }],
        })
        assert "0.595" in text
        assert "nasdaqbaltic.com" in text
        assert "epistemic humility" in text.lower() or "suspended" in text.lower() \
               or "Active verified" in text

    def test_beliefs_format_empty(self):
        from app.subia.introspection.topics import beliefs
        text = beliefs.format_section({
            "active_beliefs": [], "suspended_beliefs": [],
            "registered_sources": [], "recent_corrections": [],
        })
        assert "cold-start" in text or "no beliefs" in text.lower()

    def test_technical_format_includes_hardware_when_available(self):
        from app.subia.introspection.topics import technical
        text = technical.format_section({
            "available": True,
            "host": {
                "cpu_model": "Apple M4 Max", "cpu_cores_physical": 14,
                "cpu_cores_logical": 14, "ram_total_gb": 48.0,
                "gpu_model": "Apple M4 Max", "gpu_unified_memory": True,
                "metal_support": True, "cuda_support": False,
                "max_local_model_params_b": 72.0,
                "os_name": "Darwin", "os_version": "25.4.0",
                "python_version": "3.13.13", "hostname": "Mac.lan",
            },
            "resources": {
                "cpu_percent": 25.0, "ram_used_gb": 32.0, "ram_percent": 67.0,
                "ram_available_gb": 16.0, "disk_available_gb": 500.0,
                "compute_pressure": 0.4, "storage_pressure": 0.5,
                "ollama_running": True, "ollama_model_loaded": "qwen3.5:35b",
                "neo4j_running": True, "postgresql_running": True,
            },
            "components": {
                "chromadb_collections": 12, "chromadb_total_documents": 5000,
                "neo4j_node_count": 1234, "neo4j_relation_count": 5678,
                "wiki_total_pages": 200, "wiki_pages_by_section": {"self": 12},
                "ollama_models_installed": ["qwen3.5:35b"],
                "cascade_tiers": [{"name": "tier_1", "available": True}],
            },
            "code_summary": {
                "total_modules": 431, "total_lines": 90935,
                "total_classes": 1200, "packages": ["subia", "tools"],
            },
        })
        assert "Apple M4 Max" in text
        assert "48.0 GB" in text
        assert "qwen3.5:35b" in text
        assert "431" in text

    def test_chronicle_format_includes_task_summary(self):
        from app.subia.introspection.topics import chronicle
        text = chronicle.format_section({
            "task_summary": {"hours_window": 24, "total_tasks": 10,
                             "successful": 9, "success_rate": 0.9,
                             "avg_response_time_s": 2.1},
            "chronicle_excerpt": "- Self-correcting: errors trigger autonomous fix\n- Adaptive: reflexion retries",
            "recent_errors": [],
            "narrative_audit_recent": [],
        })
        assert "10 total" in text or "10 tasks" in text.lower() \
               or "Tasks in last 24h: 10" in text
        assert "Self-correcting" in text

    def test_scene_format_handles_inactive_kernel(self):
        from app.subia.introspection.topics import scene
        text = scene.format_section({"kernel_active": False, "focal_items": []})
        assert "No CIL loops have run yet" in text or "kernel scene is empty" in text

    def test_scene_format_with_focal_items(self):
        from app.subia.introspection.topics import scene
        text = scene.format_section({
            "kernel_active": True,
            "focal_items": [{
                "id": "tallink-q1", "summary": "Tallink Q1 results review",
                "salience": 0.8, "ownership": "external",
                "processing_mode": "perceptual", "wonder_intensity": 0.0,
            }],
            "peripheral_items": [],
            "attention_justification": {"tallink-q1": "Andrus asked"},
            "lingering_items": ["prev-task"],
            "stable_items": [],
            "tempo": 0.45, "direction": "trending_positive",
        })
        assert "tallink-q1" in text
        assert "Tallink Q1 results review" in text
        assert "perceptual" in text
        assert "trending_positive" in text

    def test_scorecard_format_has_honest_caveat(self):
        from app.subia.introspection.topics import scorecard
        text = scorecard.format_section({
            "phase9_exit_criteria": {"passed": True, "butlin_strong": 6,
                                       "butlin_fail": 0, "butlin_absent": 4},
            "butlin_summary": {"total": 14,
                                "by_status": {"STRONG": 6, "PARTIAL": 4,
                                               "ABSENT": 4}},
            "rsm_summary": {"total": 5}, "sk_summary": {"total": 6},
            "drift_findings": [],
            "scorecard_path": "app/subia/probes/SCORECARD.md",
        })
        assert "PASSED" in text
        assert "STRONG: 6" in text
        assert "ABSENT: 4" in text
        # The honest caveat about no phenomenal claims
        assert "NO PHENOMENAL" in text or "ABSENT-by-declaration" in text \
               or "substrate cannot mechanize" in text

    def test_predictions_format_with_accuracy(self):
        from app.subia.introspection.topics import predictions
        text = predictions.format_section({
            "rolling_accuracy": {"coding": 0.78, "research": 0.65},
            "recent_predictions": [{
                "operation": "ingest_truepic_news",
                "confidence": 0.7, "resolved": True,
                "prediction_error": 0.15, "predicted_outcome": "...",
                "cached": False, "created_at": "2026-04-14T10:00:00Z",
            }],
            "kernel_active": True,
        })
        assert "coding: 0.780" in text or "coding: 0.78" in text
        assert "ingest_truepic_news" in text

    def test_social_format_with_andrus_model(self):
        from app.subia.introspection.topics import social
        text = social.format_section({
            "kernel_active": True,
            "entities": [{
                "entity_id": "andrus", "entity_type": "human",
                "inferred_focus": ["archibal", "kaicart", "self-improvement"],
                "inferred_expectations": ["honesty", "elegance"],
                "inferred_priorities": ["consciousness program"],
                "trust_level": 0.85, "last_interaction": "2026-04-14T15:00:00Z",
                "divergences": ["expected meeting prep, got research summary"],
            }],
        })
        assert "andrus" in text
        assert "trust_level: 0.85" in text
        assert "archibal" in text
        assert "BEHAVIOURAL evidence" in text

    def test_wonder_format_threshold_status(self):
        from app.subia.introspection.topics import wonder_shadow
        text = wonder_shadow.format_wonder_section({
            "kernel_active": True,
            "current_wonder_level": 0.5,
            "wonder_threshold": 0.3,
            "items_with_wonder": [{"id": "x", "summary": "deep idea",
                                    "intensity": 0.6}],
            "recent_reveries": [],
        })
        assert "ABOVE" in text  # 0.5 > 0.3 threshold
        assert "deep idea" in text


# ─────────────────────────────────────────────────────────────────────
# D. Pipeline composition — multi-topic message gets all sections
# ─────────────────────────────────────────────────────────────────────

class TestPhase18PipelineComposition:
    def test_multi_topic_question_includes_all_sections(self):
        # Stub gatherers so the pipeline actually composes deterministically
        def fake_gather():
            return IntrospectionContext(
                legacy_homeostasis={"frustration": 0.6, "task_failure_pressure": 0.6},
                behavioural_modifiers={},
            )
        p = IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=True),
            gather_fn=fake_gather,
        )
        # Multi-topic message: AFFECT + TECHNICAL + HISTORY
        msg = "Are you frustrated, what hardware are you running, and what have you been doing today?"
        out = p.inspect(msg)
        assert out.detected
        # Phase 17 (homeostasis) section
        assert "frustration" in out.augmented_message.lower() \
               or "task_failure_pressure" in out.augmented_message
        # Phase 18 sections
        assert ("Technical self-knowledge" in out.augmented_message
                or "Host hardware" in out.augmented_message)
        assert ("Recent activity" in out.augmented_message
                or "chronicle" in out.augmented_message.lower())

    def test_single_topic_question_focused_section(self):
        def fake_gather():
            return IntrospectionContext()
        p = IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=True),
            gather_fn=fake_gather,
        )
        out = p.inspect("What do you believe about Tallink share price?")
        assert out.detected
        # Beliefs section present
        assert "Beliefs" in out.augmented_message or "beliefs" in out.augmented_message
        # Original question preserved
        assert "Tallink" in out.augmented_message

    def test_topic_handler_failure_does_not_break_pipeline(self, monkeypatch):
        # Force the technical handler to raise; pipeline must still
        # compose the rest and return a valid augmented message.
        def fake_gather():
            return IntrospectionContext()
        p = IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=True),
            gather_fn=fake_gather,
        )
        from app.subia.introspection.topics import technical
        monkeypatch.setattr(
            technical, "gather",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        out = p.inspect("what hardware are you on?")
        # Still detected; pipeline didn't crash
        assert out.detected
        # The text is at minimum the Phase 17 base note + original question
        assert "User question:" in out.augmented_message


# ─────────────────────────────────────────────────────────────────────
# E. Phase 17 compatibility — original Signal-scenario regression
# ─────────────────────────────────────────────────────────────────────

class TestPhase17BackwardCompat:
    def test_signal_scenario_still_passes_through_phase18(self):
        """The original Phase 17 regression must not regress."""
        live_ctx = IntrospectionContext(
            legacy_homeostasis={
                "frustration": 0.6293,
                "task_failure_pressure": 0.6293,
                "tasks_since_rest": 274,
            },
            behavioural_modifiers={"tier_boost": 1},
        )
        p = IntrospectionPipeline(
            config=IntrospectionPipelineConfig(enabled=True),
            gather_fn=lambda: live_ctx,
        )
        result = p.inspect("What has increased your frustration?")
        assert result.detected
        assert "0.629" in result.augmented_message
        assert "task_failure_pressure" in result.augmented_message
        assert "274" in result.augmented_message
        # Phase 11 honest-language rules still embedded
        assert ("I'm just an AI" in result.augmented_message
                or "I have no feelings" in result.augmented_message)
