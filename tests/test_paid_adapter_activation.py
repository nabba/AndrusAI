"""Tests for the 2026-04-26 paid-adapter activation chain.

The user's session ask was: "prepare the system so if I enter keys for
Apollo and Proxycurl these services will become automatically
operational."

The activation chain has 7 distinct points where it could break:

  1. Env vars must reach the gateway container         — docker-compose passthrough
  2. Adapters must register at orchestrator load time  — research_adapters.install()
  3. source_priority must INCLUDE the adapter names    — default_source_priority()
  4. Field-keys in caller specs must match adapter
     _SUPPORTED_FIELDS                                 — _MATRIX_FIELD_HINTS alignment
  5. is_configured() must return True when env set     — adapter contract
  6. Status visibility (log + endpoint)                — get_paid_adapter_status
  7. Adding a key must be a "set env + restart" — no   — dynamic re-evaluation
     code change required

These tests pin all seven so adding APOLLO_API_KEY or PROXYCURL_API_KEY
is the ONLY action needed. No code edit, no spec rewrite, no field-key
mapping fix.
"""
from __future__ import annotations

import os
import pytest

from tests._v2_shim import install_settings_shim
install_settings_shim()


# ══════════════════════════════════════════════════════════════════════
# Point 1 — docker-compose passthrough (text inspection of YAML)
# ══════════════════════════════════════════════════════════════════════

class TestDockerComposeEnvPassthrough:
    """The compose file must declare the env vars or they never enter
    the container. Tested by reading the file content — we don't need
    Docker running for this test."""

    def test_apollo_key_declared(self):
        from pathlib import Path
        compose_text = (
            Path(__file__).resolve().parent.parent / "docker-compose.yml"
        ).read_text()
        assert "APOLLO_API_KEY: ${APOLLO_API_KEY:-}" in compose_text, (
            "APOLLO_API_KEY must be passed through docker-compose to the "
            "gateway service. Without this, setting it in .env has no effect."
        )

    def test_proxycurl_key_declared(self):
        from pathlib import Path
        compose_text = (
            Path(__file__).resolve().parent.parent / "docker-compose.yml"
        ).read_text()
        assert "PROXYCURL_API_KEY: ${PROXYCURL_API_KEY:-}" in compose_text


# ══════════════════════════════════════════════════════════════════════
# Point 2 — adapter registration
# ══════════════════════════════════════════════════════════════════════

class TestAdapterRegistration:
    """install_paid_adapters() must register both adapters with the
    research orchestrator's _ADAPTERS registry. Always — even when keys
    are absent — so tests and dev environments hit a clean
    'no data' result rather than 'source not found'."""

    def test_apollo_registered(self):
        from app.tools.research_orchestrator import (
            install_paid_adapters, _ADAPTERS,
        )
        install_paid_adapters()
        assert "apollo" in _ADAPTERS, (
            "apollo adapter must be in the orchestrator's registry"
        )

    def test_linkedin_data_registered(self):
        from app.tools.research_orchestrator import (
            install_paid_adapters, _ADAPTERS,
        )
        install_paid_adapters()
        assert "linkedin_data" in _ADAPTERS

    def test_sales_navigator_alias_registered(self):
        """linkedin_data also registers under sales_navigator for callers
        who use the more familiar name."""
        from app.tools.research_orchestrator import (
            install_paid_adapters, _ADAPTERS,
        )
        install_paid_adapters()
        assert "sales_navigator" in _ADAPTERS


# ══════════════════════════════════════════════════════════════════════
# Point 3 — dynamic source_priority
# ══════════════════════════════════════════════════════════════════════

class TestDefaultSourcePriority:
    """default_source_priority() must include paid adapters when their
    env keys are set, and exclude them when not. Without this, even a
    registered adapter is skipped because it isn't in the chain."""

    def test_no_keys_excludes_paid_adapters(self, monkeypatch):
        monkeypatch.delenv("APOLLO_API_KEY", raising=False)
        monkeypatch.delenv("PROXYCURL_API_KEY", raising=False)
        from app.tools.research_orchestrator import default_source_priority
        priority = default_source_priority()
        assert "apollo" not in priority
        assert "linkedin_data" not in priority
        # Free chain still present
        assert "regulator" in priority
        assert "company_site" in priority
        assert "search" in priority

    def test_apollo_key_includes_apollo_in_chain(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test_key_123")
        monkeypatch.delenv("PROXYCURL_API_KEY", raising=False)
        from app.tools.research_orchestrator import default_source_priority
        priority = default_source_priority()
        assert "apollo" in priority
        assert "linkedin_data" not in priority

    def test_proxycurl_key_includes_linkedin_data(self, monkeypatch):
        monkeypatch.delenv("APOLLO_API_KEY", raising=False)
        monkeypatch.setenv("PROXYCURL_API_KEY", "test_proxy_456")
        from app.tools.research_orchestrator import default_source_priority
        priority = default_source_priority()
        assert "apollo" not in priority
        assert "linkedin_data" in priority

    def test_both_keys_includes_both_adapters(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test_key_123")
        monkeypatch.setenv("PROXYCURL_API_KEY", "test_proxy_456")
        from app.tools.research_orchestrator import default_source_priority
        priority = default_source_priority()
        assert "apollo" in priority
        assert "linkedin_data" in priority

    def test_chain_order_free_then_paid_then_search(self, monkeypatch):
        """Cost-conscious ordering: free adapters tried first, then
        paid (which cost per call), then Brave search as final fallback."""
        monkeypatch.setenv("APOLLO_API_KEY", "x")
        monkeypatch.setenv("PROXYCURL_API_KEY", "y")
        from app.tools.research_orchestrator import default_source_priority
        priority = default_source_priority()
        # Indices
        i_regulator = priority.index("regulator")
        i_company   = priority.index("company_site")
        i_apollo    = priority.index("apollo")
        i_linkedin  = priority.index("linkedin_data")
        i_search    = priority.index("search")
        assert i_regulator < i_apollo, "free adapters must come before paid"
        assert i_company   < i_apollo, "free adapters must come before paid"
        assert i_apollo    < i_search, "paid before Brave-search fallback"
        assert i_linkedin  < i_search


# ══════════════════════════════════════════════════════════════════════
# Point 4 — field-key alignment
# ══════════════════════════════════════════════════════════════════════

class TestMatrixFieldKeysMatchAdapters:
    """The matrix-route helper builds spec.fields with keys like
    ``head_of_sales`` / ``head_of_sales_linkedin``. Those keys MUST
    exist in the adapter _SUPPORTED_FIELDS sets — otherwise the
    adapter sees an unknown key and returns None silently, defeating
    the entire integration even after the user adds their key."""

    def test_head_of_sales_recognized_by_apollo(self):
        from app.tools.research_adapters.apollo import _SUPPORTED_FIELDS
        assert "head_of_sales" in _SUPPORTED_FIELDS

    def test_head_of_sales_linkedin_recognized_by_apollo(self):
        from app.tools.research_adapters.apollo import _SUPPORTED_FIELDS
        assert "head_of_sales_linkedin" in _SUPPORTED_FIELDS

    def test_head_of_sales_recognized_by_linkedin_data(self):
        from app.tools.research_adapters.linkedin_data import _SUPPORTED_FIELDS
        assert "head_of_sales" in _SUPPORTED_FIELDS

    def test_head_of_sales_linkedin_recognized_by_linkedin_data(self):
        from app.tools.research_adapters.linkedin_data import _SUPPORTED_FIELDS
        assert "head_of_sales_linkedin" in _SUPPORTED_FIELDS

    def test_matrix_field_hints_use_canonical_keys(self):
        """The orchestrator's matrix-route hint table must produce
        canonical keys — not 'head_of_sales_name', not 'sales_lead', etc."""
        from app.agents.commander.orchestrator import _MATRIX_FIELD_HINTS
        from app.tools.research_adapters.apollo import _SUPPORTED_FIELDS

        # Every "head of sales" / "vp sales" / "cro" hint should map to
        # a key supported by Apollo. (CEO/CTO/CFO are NOT in Apollo —
        # those legitimately use non-paid keys.)
        for hint_text in ("head of sales", "vp sales", "cro"):
            spec = _MATRIX_FIELD_HINTS[hint_text]
            assert spec["key"] in _SUPPORTED_FIELDS, (
                f"hint {hint_text!r} produces key {spec['key']!r} which "
                f"is NOT in Apollo's _SUPPORTED_FIELDS — adapter would "
                f"silently no-op. Canonical Apollo keys: {sorted(_SUPPORTED_FIELDS)}"
            )

    def test_linkedin_profile_hint_uses_canonical_key(self):
        from app.agents.commander.orchestrator import _MATRIX_FIELD_HINTS
        from app.tools.research_adapters.apollo import _SUPPORTED_FIELDS
        spec = _MATRIX_FIELD_HINTS["linkedin profile"]
        assert spec["key"] in _SUPPORTED_FIELDS

    def test_field_hint_marks_known_hard_for_paid_only_fields(self):
        """Personal-LinkedIn fields are flagged known_hard so the
        orchestrator's coverage report can explain why a row is empty
        when no paid key is set."""
        from app.agents.commander.orchestrator import _MATRIX_FIELD_HINTS
        spec = _MATRIX_FIELD_HINTS["linkedin profile"]
        assert spec.get("known_hard") is True
        # Must mention Apollo or Proxycurl in the reason so the user
        # knows what to enable.
        reason = spec.get("reason", "").lower()
        assert "apollo" in reason or "proxycurl" in reason or "sales navigator" in reason


# ══════════════════════════════════════════════════════════════════════
# Point 5 — is_configured() contract
# ══════════════════════════════════════════════════════════════════════

class TestAdapterIsConfigured:

    def test_apollo_is_configured_only_when_env_set(self, monkeypatch):
        from app.tools.research_adapters.apollo import is_configured
        monkeypatch.delenv("APOLLO_API_KEY", raising=False)
        assert is_configured() is False
        monkeypatch.setenv("APOLLO_API_KEY", "test_key")
        assert is_configured() is True
        monkeypatch.setenv("APOLLO_API_KEY", "  ")  # whitespace-only
        assert is_configured() is False, (
            "whitespace-only key must not be considered configured"
        )

    def test_proxycurl_check_via_get_paid_adapter_status(self, monkeypatch):
        from app.tools.research_orchestrator import get_paid_adapter_status
        monkeypatch.delenv("PROXYCURL_API_KEY", raising=False)
        assert get_paid_adapter_status()["linkedin_data"] is False
        monkeypatch.setenv("PROXYCURL_API_KEY", "test_proxy")
        assert get_paid_adapter_status()["linkedin_data"] is True


# ══════════════════════════════════════════════════════════════════════
# Point 6 — visibility (status structure)
# ══════════════════════════════════════════════════════════════════════

class TestAdapterStatusShape:
    """The status payload is consumed by the dashboard endpoint. Pin
    its keys so a refactor doesn't silently break the UI."""

    def test_status_keys_stable(self, monkeypatch):
        monkeypatch.delenv("APOLLO_API_KEY", raising=False)
        monkeypatch.delenv("PROXYCURL_API_KEY", raising=False)
        from app.tools.research_orchestrator import get_paid_adapter_status
        status = get_paid_adapter_status()
        assert set(status.keys()) == {"apollo", "linkedin_data"}
        assert all(isinstance(v, bool) for v in status.values())


# ══════════════════════════════════════════════════════════════════════
# Point 7 — Fix B uses dynamic priority (downstream of point 3)
# ══════════════════════════════════════════════════════════════════════

class TestMatrixRouteUsesDynamicPriority:
    """When the matrix route fires, its injected spec must use the
    dynamic source priority — otherwise the user adding a key has no
    effect on the spec the agent sees."""

    def test_injected_spec_has_apollo_when_keyed(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "test_key")
        monkeypatch.delenv("PROXYCURL_API_KEY", raising=False)

        from app.agents.commander.orchestrator import _try_matrix_research_route
        out = _try_matrix_research_route(
            "find head of sales for these 10 PSPs",
        )
        assert out is not None
        body = out[0]["task"]
        # Spec is JSON-embedded in the task body
        assert '"apollo"' in body, (
            "matrix route's injected spec must include apollo in "
            "source_priority when APOLLO_API_KEY is set"
        )

    def test_injected_spec_excludes_apollo_when_unkeyed(self, monkeypatch):
        monkeypatch.delenv("APOLLO_API_KEY", raising=False)
        monkeypatch.delenv("PROXYCURL_API_KEY", raising=False)

        from app.agents.commander.orchestrator import _try_matrix_research_route
        out = _try_matrix_research_route(
            "find head of sales for these 10 PSPs",
        )
        assert out is not None
        body = out[0]["task"]
        # Source priority list shouldn't include apollo without key
        # (the literal string "apollo" might appear elsewhere in the
        # known_hard field reason — so we check the source_priority
        # array specifically)
        import re
        m = re.search(r'"source_priority":\s*\[([^\]]+)\]', body)
        assert m, "injected spec must contain a source_priority array"
        chain = m.group(1)
        assert '"apollo"' not in chain
        assert '"linkedin_data"' not in chain
