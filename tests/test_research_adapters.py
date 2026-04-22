"""Tests for app.tools.research_adapters — Apollo + Sales Navigator /
Proxycurl adapters.

All HTTP is mocked; no live keys required to run the suite.  These
tests prove:

* Missing API key → adapter returns None (graceful unavailability)
* Wrong field_key → adapter returns None (clean contract)
* Valid response → each supported field extracted correctly
* HTTP error (401, 500, timeout) → returns None without raising
* Empty results → returns None
* Locked Apollo email → emails marked unavailable; other fields still fill
* Proxycurl → Brave fallback ordering
* install() hooks register adapters under the expected names
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.tools.research_adapters import apollo as apollo_mod
from app.tools.research_adapters import linkedin_data as ld_mod
from app.tools import research_orchestrator as ro


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _clear_caches():
    apollo_mod._CACHE.clear()
    ld_mod._CACHE.clear()
    yield
    apollo_mod._CACHE.clear()
    ld_mod._CACHE.clear()


@pytest.fixture
def subject():
    return {"id": "s1", "name": "Montonio", "market": "Estonia",
            "homepage": "https://www.montonio.com"}


def _mock_response(status: int = 200, json_body: dict | None = None,
                   text: str = ""):
    m = MagicMock()
    m.status_code = status
    m.json = MagicMock(return_value=json_body or {})
    m.text = text
    return m


# ══════════════════════════════════════════════════════════════════════
# Apollo adapter
# ══════════════════════════════════════════════════════════════════════

class TestApolloMissingKey:

    def test_no_key_returns_none_for_every_field(self, subject, monkeypatch):
        monkeypatch.delenv("APOLLO_API_KEY", raising=False)
        for key in ("head_of_sales", "head_of_sales_linkedin",
                    "head_of_sales_email"):
            assert apollo_mod.apollo_adapter(subject, {"key": key}) is None

    def test_is_configured_reflects_env(self, monkeypatch):
        monkeypatch.delenv("APOLLO_API_KEY", raising=False)
        assert apollo_mod.is_configured() is False
        monkeypatch.setenv("APOLLO_API_KEY", "x")
        assert apollo_mod.is_configured() is True
        monkeypatch.setenv("APOLLO_API_KEY", "")
        assert apollo_mod.is_configured() is False


class TestApolloWrongFieldKey:

    def test_unknown_field_key_returns_none(self, subject, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "key123")
        # Should not even hit the HTTP layer
        with patch("requests.post") as post:
            out = apollo_mod.apollo_adapter(subject, {"key": "homepage"})
        assert out is None
        post.assert_not_called()


class TestApolloHappyPath:
    """Valid Apollo response → each field key extracts correctly."""

    APOLLO_PERSON = {
        "first_name": "Jaan",
        "last_name": "Tamm",
        "title": "Head of Sales",
        "linkedin_url": "https://www.linkedin.com/in/jaan-tamm-12345",
        "email": "jaan@montonio.com",
    }

    @pytest.fixture
    def apollo_env(self, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "key123")

    def test_head_of_sales_name_plus_title(self, apollo_env, subject):
        with patch("requests.post", return_value=_mock_response(
            200, {"people": [self.APOLLO_PERSON]},
        )) as post:
            out = apollo_mod.apollo_adapter(subject, {"key": "head_of_sales"})
        assert out == "Jaan Tamm (Head of Sales)"
        # Apollo called with the right payload shape
        call_kwargs = post.call_args.kwargs
        assert call_kwargs["headers"]["X-Api-Key"] == "key123"
        assert call_kwargs["json"]["q_organization_domains"] == "montonio.com"
        assert "Head of Sales" in call_kwargs["json"]["person_titles"]

    def test_linkedin_url(self, apollo_env, subject):
        with patch("requests.post", return_value=_mock_response(
            200, {"people": [self.APOLLO_PERSON]},
        )):
            out = apollo_mod.apollo_adapter(
                subject, {"key": "head_of_sales_linkedin"},
            )
        assert out == "https://www.linkedin.com/in/jaan-tamm-12345"

    def test_linkedin_alias_key(self, apollo_env, subject):
        with patch("requests.post", return_value=_mock_response(
            200, {"people": [self.APOLLO_PERSON]},
        )):
            out = apollo_mod.apollo_adapter(
                subject, {"key": "linkedin_head_of_sales"},
            )
        assert out == "https://www.linkedin.com/in/jaan-tamm-12345"

    def test_email_when_unlocked(self, apollo_env, subject):
        with patch("requests.post", return_value=_mock_response(
            200, {"people": [self.APOLLO_PERSON]},
        )):
            out = apollo_mod.apollo_adapter(
                subject, {"key": "head_of_sales_email"},
            )
        assert out == "jaan@montonio.com"

    def test_cached_across_fields(self, apollo_env, subject):
        """Fetching 3 different fields for the same subject should hit
        the HTTP layer exactly once (cache preserves the person)."""
        with patch("requests.post", return_value=_mock_response(
            200, {"people": [self.APOLLO_PERSON]},
        )) as post:
            apollo_mod.apollo_adapter(subject, {"key": "head_of_sales"})
            apollo_mod.apollo_adapter(subject, {"key": "head_of_sales_linkedin"})
            apollo_mod.apollo_adapter(subject, {"key": "head_of_sales_email"})
        assert post.call_count == 1


class TestApolloEmailLocked:

    def test_locked_email_returns_none_for_email_field(self, subject, monkeypatch):
        """Apollo returns ``email_not_unlocked@...`` when the caller
        hasn't burned credits to unlock that person's email.  Treat as
        miss — name + LinkedIn still resolve from the same row."""
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        locked = {
            "first_name": "Jaan",
            "last_name": "Tamm",
            "title": "Head of Sales",
            "linkedin_url": "https://www.linkedin.com/in/j",
            "email": "email_not_unlocked@montonio.com",
        }
        with patch("requests.post", return_value=_mock_response(
            200, {"people": [locked]},
        )):
            name = apollo_mod.apollo_adapter(subject, {"key": "head_of_sales"})
            email = apollo_mod.apollo_adapter(
                subject, {"key": "head_of_sales_email"},
            )
            linkedin = apollo_mod.apollo_adapter(
                subject, {"key": "head_of_sales_linkedin"},
            )
        assert name == "Jaan Tamm (Head of Sales)"
        assert email is None   # the one field that needed the unlock
        assert linkedin == "https://www.linkedin.com/in/j"


class TestApolloErrorPaths:

    def test_auth_error_returns_none(self, subject, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "bad")
        with patch("requests.post", return_value=_mock_response(401, text="no")):
            out = apollo_mod.apollo_adapter(subject, {"key": "head_of_sales"})
        assert out is None

    def test_server_error_returns_none(self, subject, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        with patch("requests.post", return_value=_mock_response(500)):
            out = apollo_mod.apollo_adapter(subject, {"key": "head_of_sales"})
        assert out is None

    def test_network_timeout_returns_none(self, subject, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        with patch("requests.post", side_effect=Exception("timeout")):
            out = apollo_mod.apollo_adapter(subject, {"key": "head_of_sales"})
        assert out is None

    def test_empty_people_returns_none(self, subject, monkeypatch):
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        with patch("requests.post", return_value=_mock_response(
            200, {"people": []},
        )):
            out = apollo_mod.apollo_adapter(subject, {"key": "head_of_sales"})
        assert out is None

    def test_subject_without_domain_returns_none(self, monkeypatch):
        """A subject we can't resolve to a domain short-circuits
        before the HTTP call."""
        monkeypatch.setenv("APOLLO_API_KEY", "k")
        subj = {"name": "Anonymous Inc", "market": "EE"}  # no homepage
        with patch("requests.post") as post:
            out = apollo_mod.apollo_adapter(subj, {"key": "head_of_sales"})
        assert out is None
        post.assert_not_called()


class TestApolloDomainExtraction:

    @pytest.mark.parametrize("homepage,expected", [
        ("https://www.montonio.com", "montonio.com"),
        ("https://montonio.com/about", "montonio.com"),
        ("http://www.decta.com/", "decta.com"),
        ("montonio.com", "montonio.com"),
        ("", ""),
    ])
    def test_various_homepage_forms(self, homepage, expected):
        assert apollo_mod._get_domain({"homepage": homepage}) == expected


# ══════════════════════════════════════════════════════════════════════
# LinkedIn-data / Sales Navigator adapter
# ══════════════════════════════════════════════════════════════════════

class TestLinkedinWrongFieldKey:

    def test_unsupported_field_returns_none(self, subject):
        assert ld_mod.linkedin_data_adapter(subject, {"key": "homepage"}) is None
        assert ld_mod.linkedin_data_adapter(subject, {"key": "sales_email"}) is None


class TestLinkedinProxycurlHappyPath:

    EMPLOYEE = {
        "profile": {
            "first_name": "Anna",
            "last_name": "Ivanova",
            "headline": "Head of Sales @ DECTA",
        },
        "profile_url": "https://www.linkedin.com/in/anna-iv",
    }

    @pytest.fixture
    def env(self, monkeypatch):
        monkeypatch.setenv("PROXYCURL_API_KEY", "pkey")

    def test_proxycurl_name(self, env, subject):
        def _get(url, **kwargs):
            if "company/resolve" in url:
                return _mock_response(200, {"url": "https://linkedin.com/company/decta"})
            if "employee/search" in url:
                return _mock_response(200, {"employees": [self.EMPLOYEE]})
            return _mock_response(404)

        with patch("requests.get", side_effect=_get):
            out = ld_mod.linkedin_data_adapter(
                subject, {"key": "head_of_sales"},
            )
        assert "Anna Ivanova" in out
        assert "proxycurl" in out  # provenance tag
        assert "Head of Sales @ DECTA" in out

    def test_proxycurl_linkedin_url(self, env, subject):
        def _get(url, **kwargs):
            if "company/resolve" in url:
                return _mock_response(200, {"url": "https://linkedin.com/company/decta"})
            return _mock_response(200, {"employees": [self.EMPLOYEE]})

        with patch("requests.get", side_effect=_get):
            out = ld_mod.linkedin_data_adapter(
                subject, {"key": "head_of_sales_linkedin"},
            )
        assert out == "https://www.linkedin.com/in/anna-iv"


class TestLinkedinProxycurlFailsToFallback:
    """When Proxycurl 5xx's or returns empty, the adapter must fall
    through to Brave structural search."""

    BRAVE_HIT = {
        "title": "Kaspar Kivi - Head of Sales - Montonio - LinkedIn",
        "url": "https://www.linkedin.com/in/kaspar-kivi",
        "description": "Head of Sales at Montonio...",
    }

    def test_proxycurl_error_falls_through_to_brave(self, subject, monkeypatch):
        monkeypatch.setenv("PROXYCURL_API_KEY", "pkey")
        # Proxycurl errors out on the first call
        with patch("requests.get", return_value=_mock_response(500)):
            with patch(
                "app.tools.web_search.search_brave",
                return_value=[self.BRAVE_HIT],
            ) as brave:
                out = ld_mod.linkedin_data_adapter(
                    subject, {"key": "head_of_sales"},
                )
        assert out is not None
        assert "Kaspar Kivi" in out
        assert "brave" in out  # provenance tag
        # We must have tried Brave at least once
        assert brave.call_count >= 1

    def test_no_proxycurl_key_uses_brave_directly(self, subject, monkeypatch):
        monkeypatch.delenv("PROXYCURL_API_KEY", raising=False)
        # No Proxycurl call happens at all
        with patch("requests.get") as req_get:
            with patch(
                "app.tools.web_search.search_brave",
                return_value=[self.BRAVE_HIT],
            ):
                out = ld_mod.linkedin_data_adapter(
                    subject, {"key": "head_of_sales_linkedin"},
                )
        assert out == "https://www.linkedin.com/in/kaspar-kivi"
        req_get.assert_not_called()


class TestLinkedinNoProvidersHit:

    def test_all_providers_return_nothing(self, subject, monkeypatch):
        monkeypatch.delenv("PROXYCURL_API_KEY", raising=False)
        with patch("app.tools.web_search.search_brave", return_value=[]):
            out = ld_mod.linkedin_data_adapter(
                subject, {"key": "head_of_sales"},
            )
        assert out is None

    def test_brave_returns_non_linkedin_urls(self, subject, monkeypatch):
        """Brave returning non-LinkedIn URLs must not be treated as hit."""
        monkeypatch.delenv("PROXYCURL_API_KEY", raising=False)
        with patch("app.tools.web_search.search_brave", return_value=[
            {"title": "foo", "url": "https://twitter.com/foo", "description": ""},
            {"title": "bar", "url": "https://example.com", "description": ""},
        ]):
            out = ld_mod.linkedin_data_adapter(
                subject, {"key": "head_of_sales"},
            )
        assert out is None


class TestLinkedinCaching:

    def test_repeated_lookups_use_cache(self, subject, monkeypatch):
        monkeypatch.setenv("PROXYCURL_API_KEY", "pkey")

        def _get(url, **kwargs):
            if "company/resolve" in url:
                return _mock_response(200, {"url": "https://linkedin.com/c"})
            return _mock_response(200, {"employees": [{
                "profile": {
                    "first_name": "X", "last_name": "Y",
                    "headline": "Head of Sales",
                },
                "profile_url": "https://www.linkedin.com/in/xy",
            }]})

        with patch("requests.get", side_effect=_get) as req_get:
            # Two field lookups for the same subject = one HTTP pair
            ld_mod.linkedin_data_adapter(subject, {"key": "head_of_sales"})
            ld_mod.linkedin_data_adapter(
                subject, {"key": "head_of_sales_linkedin"},
            )
        # 2 calls for first lookup (resolve + search), 0 for the second.
        assert req_get.call_count == 2


# ══════════════════════════════════════════════════════════════════════
# install() — adapters appear under expected names
# ══════════════════════════════════════════════════════════════════════

class TestInstallation:

    def test_apollo_registered(self):
        # Clear any prior state and re-install
        ro._ADAPTERS.pop("apollo", None)
        apollo_mod.install()
        assert ro._ADAPTERS["apollo"] is apollo_mod.apollo_adapter

    def test_linkedin_registered_under_both_names(self):
        ro._ADAPTERS.pop("linkedin_data", None)
        ro._ADAPTERS.pop("sales_navigator", None)
        ld_mod.install()
        assert ro._ADAPTERS["linkedin_data"] is ld_mod.linkedin_data_adapter
        assert ro._ADAPTERS["sales_navigator"] is ld_mod.linkedin_data_adapter

    def test_install_paid_adapters_is_idempotent(self):
        from app.tools.research_adapters import install as install_all
        # Register twice — must not error
        install_all()
        install_all()
        assert "apollo" in ro._ADAPTERS
        assert "sales_navigator" in ro._ADAPTERS


class TestOrchestratorIntegration:
    """End-to-end: orchestrator's @tool wrapper calls
    install_paid_adapters(), then the adapter chain fills the cells."""

    def test_orchestrator_picks_up_adapters_on_first_invocation(
        self, monkeypatch,
    ):
        # Forget any prior registration state
        ro._ADAPTERS.pop("apollo", None)
        ro._ADAPTERS.pop("sales_navigator", None)
        # Pretend Apollo is configured
        monkeypatch.setenv("APOLLO_API_KEY", "k")

        apollo_person = {
            "first_name": "A", "last_name": "B",
            "title": "Head of Sales",
            "linkedin_url": "https://www.linkedin.com/in/ab",
            "email": "a@example.com",
        }
        with patch("requests.post", return_value=_mock_response(
            200, {"people": [apollo_person]},
        )):
            import json
            out = ro.research_orchestrator.run(spec_json=json.dumps({
                "subjects": [{"id": "s1", "name": "Example",
                              "market": "EE",
                              "homepage": "https://example.com"}],
                "fields": [
                    {"key": "head_of_sales"},
                    {"key": "head_of_sales_linkedin"},
                ],
                "source_priority": ["apollo"],
                "max_subjects_in_parallel": 1,
            }))

        parsed = json.loads(out)
        row = parsed["rows"][0]
        assert row["values"]["head_of_sales"]["value"] == "A B (Head of Sales)"
        assert row["values"]["head_of_sales_linkedin"]["value"] == (
            "https://www.linkedin.com/in/ab"
        )
        assert row["values"]["head_of_sales"]["source"] == "apollo"
