"""Tests for Phase A.3 — pre_classified_zone parameter on
create_request (2026-05-22).

Closes the gap where the verified plan promised this parameter but
it never landed. Multi-step callers (autonomous executor, batch
pipelines) can now pre-classify ONCE and have the same zone reach
every CR.

Covers:
  * Default (None) → zone_for_path is called as before
  * Explicit zone → zone_for_path NOT called; provided zone wins
  * Provided zone propagates to is_high_stakes_zone check
  * review_for_change_request invoked with the provided zone
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

_mock_psycopg2 = MagicMock()
_mock_psycopg2.InterfaceError = type("InterfaceError", (Exception,), {})
_mock_psycopg2.OperationalError = type("OperationalError", (Exception,), {})
sys.modules.setdefault("psycopg2", _mock_psycopg2)
sys.modules.setdefault("psycopg2.pool", MagicMock())

try:
    import crewai as _real_crewai  # noqa: F401
    _crewai_available = True
except Exception:
    _crewai_available = False

if not _crewai_available:
    for _mod in ("crewai", "crewai.tools"):
        if _mod not in sys.modules:
            m = types.ModuleType(_mod)
            if _mod == "crewai.tools":
                m.tool = lambda name: (lambda fn: fn)
                m.BaseTool = type("BaseTool", (), {})
            sys.modules[_mod] = m


def _import_lifecycle():
    try:
        from app.change_requests import lifecycle
        return lifecycle
    except Exception as exc:
        pytest.skip(f"change_requests.lifecycle unavailable: {exc}")


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Stub the change-request store + audit so tests don't touch
    real persistence. We only care about the in-process control
    flow — does zone_for_path get called? Does review_for_change_request
    get the right zone? Persistence is exercised in dedicated store
    tests."""
    from app.change_requests import store as cr_store
    monkeypatch.setattr(cr_store, "save", lambda cr, audit_event=None: None)
    # _maybe_emit_diagnosis_telemetry also writes; stub it too
    from app.change_requests import lifecycle
    if hasattr(lifecycle, "_maybe_emit_diagnosis_telemetry"):
        monkeypatch.setattr(
            lifecycle, "_maybe_emit_diagnosis_telemetry",
            lambda *a, **kw: None,
        )
    yield tmp_path


class TestPreClassifiedZone:
    def test_default_none_calls_zone_for_path(
        self, isolated_store, monkeypatch,
    ):
        lifecycle = _import_lifecycle()
        # Stub zone_for_path so we can detect the call
        zone_calls = []

        def _stub_zone_for_path(path):
            zone_calls.append(path)
            from app.risk_classifier.zones import Zone
            return Zone.CHAT  # low-stakes; no review fires

        with patch(
            "app.risk_classifier.zone_for_path",
            side_effect=_stub_zone_for_path,
        ):
            try:
                lifecycle.create_request(
                    requestor="test-coder",
                    path="docs/note.md",
                    new_content="hello",
                    old_content="",
                    reason="test",
                )
            except Exception:
                # The create may fail downstream (validators, signal,
                # etc.) — we only care that zone_for_path WAS consulted
                pass
        assert zone_calls, "zone_for_path should have been called when pre_classified_zone is None"
        assert zone_calls[0] == "docs/note.md"

    def test_pre_classified_skips_zone_for_path(
        self, isolated_store, monkeypatch,
    ):
        lifecycle = _import_lifecycle()
        zone_calls = []

        def _stub_zone_for_path(path):
            zone_calls.append(path)
            from app.risk_classifier.zones import Zone
            return Zone.CHAT

        with patch(
            "app.risk_classifier.zone_for_path",
            side_effect=_stub_zone_for_path,
        ):
            try:
                lifecycle.create_request(
                    requestor="test-coder",
                    path="docs/note.md",
                    new_content="hello",
                    old_content="",
                    reason="test",
                    pre_classified_zone="financial",
                )
            except Exception:
                pass

        # zone_for_path NOT called when pre_classified_zone supplied
        assert zone_calls == [], (
            "zone_for_path should NOT be called when pre_classified_zone "
            "is supplied"
        )

    def test_pre_classified_zone_reaches_review_hook(
        self, isolated_store, monkeypatch,
    ):
        """When the pre-classified zone is high-stakes, the review hook
        should fire with that zone — not the path-derived one."""
        lifecycle = _import_lifecycle()
        captured_zones = []

        def _capture_zone_passed_to_review(cr, *, zone):
            captured_zones.append(zone)

        from app.risk_classifier import two_reasoner
        monkeypatch.setattr(
            two_reasoner, "review_for_change_request",
            _capture_zone_passed_to_review,
        )
        # is_high_stakes_zone needs to say YES for the review hook to fire
        monkeypatch.setattr(
            two_reasoner, "is_high_stakes_zone",
            lambda z: z in ("financial", "security_sensitive", "two_party"),
        )

        try:
            lifecycle.create_request(
                requestor="test-coder",
                path="docs/note.md",  # path is in low-stakes ZONE_CHAT
                new_content="hello",
                old_content="",
                reason="test",
                pre_classified_zone="financial",  # but operator says financial
            )
        except Exception:
            pass

        # Review hook ran with the OPERATOR's zone, not the path-derived one
        assert captured_zones == ["financial"], (
            f"Expected review hook to receive 'financial' (the "
            f"pre_classified_zone), got {captured_zones}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
