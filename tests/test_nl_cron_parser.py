"""Tests for app/cron/nl_parser.py — natural language → cron expression."""
import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()

from app.cron import nl_parser  # noqa: E402


class TestExtractTime:
    @pytest.mark.parametrize("text,expected", [
        ("at 7am", (7, 0)),
        ("at 7:30 am", (7, 30)),
        ("at 3pm", (15, 0)),
        ("at 11:45 pm", (23, 45)),
        ("at 12am", (0, 0)),
        ("at 12pm", (12, 0)),
        ("at 14:30", (14, 30)),
        ("at 09:00", (9, 0)),
        ("at midnight",  None),  # no digits → no match
    ])
    def test_parses_times(self, text, expected):
        assert nl_parser._extract_time(text) == expected


class TestRuleBasedParse:
    @pytest.mark.parametrize("phrase,cron", [
        ("every 5 minutes", "*/5 * * * *"),
        ("every 15 min", "*/15 * * * *"),
        ("every 2 hours", "0 */2 * * *"),
        ("every hour", "0 * * * *"),
        ("hourly", "0 * * * *"),
        ("daily at 9am", "0 9 * * *"),
        ("every day at 9:30", "30 9 * * *"),
        ("each morning", "0 8 * * *"),
        ("each evening", "0 18 * * *"),
        ("weekdays at 8am", "0 8 * * 1-5"),
        ("monday to friday", "0 9 * * 1-5"),
        ("weekends", "0 10 * * 6,0"),
        ("every monday at 10am", "0 10 * * 1"),
        ("every friday", "0 9 * * 5"),
        ("at 3pm", "0 15 * * *"),
        ("at noon", "0 12 * * *"),
        ("at midnight", "0 0 * * *"),
    ])
    def test_cases(self, phrase, cron):
        assert nl_parser._rule_based_parse(phrase) == cron

    def test_unparseable_returns_none(self):
        assert nl_parser._rule_based_parse("hey bot do the thing") is None

    def test_rejects_invalid_minute_step(self):
        # Parser only accepts 1..59
        assert nl_parser._rule_based_parse("every 99 minutes") is None


class TestNlToCron:
    def test_rule_based_path_used_first(self, monkeypatch):
        # If rule-based returns a value, LLM is never invoked
        monkeypatch.setattr(nl_parser, "_llm_parse",
                            lambda _: pytest.fail("LLM should not run"))
        assert nl_parser.nl_to_cron("every 5 minutes") == "*/5 * * * *"

    def test_llm_fallback_invoked_when_rules_fail(self, monkeypatch):
        monkeypatch.setattr(nl_parser, "_rule_based_parse", lambda _: None)
        monkeypatch.setattr(nl_parser, "_llm_parse", lambda _: "15 10 * * 3")
        assert nl_parser.nl_to_cron("third Wednesday of each month") == "15 10 * * 3"

    def test_empty_input_returns_none(self):
        assert nl_parser.nl_to_cron("") is None
        assert nl_parser.nl_to_cron("   ") is None


class TestLlmParse:
    def test_parses_valid_cron_from_llm(self, monkeypatch):
        from unittest.mock import MagicMock
        llm = MagicMock()
        llm.call = MagicMock(return_value="```\n0 6 * * 1\n```")
        monkeypatch.setattr("app.llm_factory.create_cheap_vetting_llm", lambda: llm)
        assert nl_parser._llm_parse("mondays at 6") == "0 6 * * 1"

    def test_rejects_bad_format_from_llm(self, monkeypatch):
        from unittest.mock import MagicMock
        llm = MagicMock()
        llm.call = MagicMock(return_value="not a cron expression at all")
        monkeypatch.setattr("app.llm_factory.create_cheap_vetting_llm", lambda: llm)
        assert nl_parser._llm_parse("something") is None

    def test_returns_none_when_llm_errors(self, monkeypatch):
        def boom():
            raise RuntimeError("no llm")
        monkeypatch.setattr("app.llm_factory.create_cheap_vetting_llm", boom)
        assert nl_parser._llm_parse("anything") is None


class TestDescribeCron:
    @pytest.mark.parametrize("expr,expected_contains", [
        ("* * * * *", "every minute"),
        ("0 * * * *", "every hour"),
        ("*/5 * * * *", "every 5 minutes"),
        ("0 */3 * * *", "every 3 hours"),
        ("0 9 * * 1-5", "weekdays"),
        ("0 10 * * 6,0", "weekends"),
        ("30 14 * * *", "14:30"),
    ])
    def test_describes(self, expr, expected_contains):
        out = nl_parser.describe_cron(expr)
        assert expected_contains in out

    def test_invalid_expression_returned_as_is(self):
        assert nl_parser.describe_cron("broken") == "broken"
