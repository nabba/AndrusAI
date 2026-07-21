"""Tests for the OpenRouter Fusion subsystem (app/fusion/).

Covers the parts that don't need the live gateway: class→id resolution (the
"LLM chooser"), panel/judge assembly, the extra_body merge that composes with
provider-exclusion, the per-day budget cap, and the plan_for_role gating
matrix. The live OpenRouter round-trip is a separate, operator-authorized spike.
"""

from __future__ import annotations

import pytest

# app.fusion imports only app.llm_catalog + app.paths (stdlib-clean) and reads
# runtime_settings lazily, so this should import on a bare host. importorskip
# keeps CI honest if the app package can't be imported at all.
pytest.importorskip("app.fusion")

from app.fusion import apply as fusion_apply  # noqa: E402
from app.fusion import budget, config, observe, panel  # noqa: E402
from app.fusion.apply import (  # noqa: E402
    agent_extra_body,
    fusion_state,
    inject_plugin,
    plan_for_role,
)


# ── Fake catalog (vendor families mirroring the operator's four classes) ──
FAKE_CATALOG: dict[str, dict] = {
    "gemini-flash": {
        "provider": "openrouter", "tier": "mid",
        "model_id": "openrouter/google/gemini-3.5-flash",
        "strengths": {"general": 0.80}, "cost_input_per_m": 0.30,
    },
    "gemini-pro": {
        "provider": "openrouter", "tier": "premium",
        "model_id": "openrouter/google/gemini-3.5-pro",
        "strengths": {"general": 0.92}, "cost_input_per_m": 2.00,
    },
    "qwen-max": {
        "provider": "openrouter", "tier": "premium",
        "model_id": "openrouter/qwen/qwen-3.7-max",
        "strengths": {"general": 0.88}, "cost_input_per_m": 1.00,
    },
    "kimi": {
        "provider": "openrouter", "tier": "budget",
        "model_id": "openrouter/moonshotai/kimi-k2.6",
        "strengths": {"general": 0.84}, "cost_input_per_m": 0.50,
    },
    "deepseek": {
        "provider": "openrouter", "tier": "budget",
        "model_id": "openrouter/deepseek/deepseek-v4",
        "strengths": {"general": 0.86}, "cost_input_per_m": 0.28,
    },
    "deepseek-old": {
        "provider": "openrouter", "tier": "budget",
        "model_id": "openrouter/deepseek/deepseek-v3",
        "strengths": {"general": 0.80}, "cost_input_per_m": 0.28,
        "_retired": True,
    },
    "local-qwen": {
        "provider": "ollama", "tier": "local",
        "model_id": "ollama_chat/qwen3.5",
        "strengths": {"general": 0.70},
    },
}


@pytest.fixture()
def fake_catalog(monkeypatch):
    monkeypatch.setattr(panel, "CATALOG", FAKE_CATALOG)
    return FAKE_CATALOG


# ── champion_for_class (the per-vendor "chooser") ────────────────────────

def test_champion_picks_live_not_retired(fake_catalog):
    assert panel.champion_for_class("deepseek") == "openrouter/deepseek/deepseek-v4"


def test_champion_variant_hint_wins_over_tier(fake_catalog):
    # "flash" hint must pick the mid-tier flash over the premium pro.
    assert panel.champion_for_class("google", hint="flash") == \
        "openrouter/google/gemini-3.5-flash"


def test_champion_no_hint_ranks_premium_first(fake_catalog):
    assert panel.champion_for_class("google", hint="") == \
        "openrouter/google/gemini-3.5-pro"


def test_champion_resolves_aliases(fake_catalog):
    assert panel.champion_for_class("kimi") == "openrouter/moonshotai/kimi-k2.6"
    assert panel.champion_for_class("gemini", hint="flash") == \
        "openrouter/google/gemini-3.5-flash"


def test_champion_excludes_blocked(fake_catalog):
    # v4 blocked + v3 retired ⇒ nothing left for the vendor.
    assert panel.champion_for_class(
        "deepseek", blocked={"openrouter/deepseek/deepseek-v4"}
    ) is None


def test_champion_skips_ollama_and_unknown_vendor(fake_catalog):
    assert panel.champion_for_class("nonesuch") is None
    # The only "qwen3.5" entry is an ollama local model — not openrouter.
    assert panel.champion_for_class("qwen3.5") is None


# ── resolve_panel / resolve_judge ────────────────────────────────────────

def test_resolve_panel_four_classes(fake_catalog):
    members = panel.resolve_panel(
        ["google", "qwen", "moonshotai", "deepseek"],
        hints={"google": "flash"},
    )
    assert members == [
        "openrouter/google/gemini-3.5-flash",
        "openrouter/qwen/qwen-3.7-max",
        "openrouter/moonshotai/kimi-k2.6",
        "openrouter/deepseek/deepseek-v4",
    ]


def test_resolve_panel_honours_pins(fake_catalog):
    members = panel.resolve_panel(
        ["google", "deepseek"],
        pins={"google": "openrouter/google/custom-pinned"},
    )
    assert members[0] == "openrouter/google/custom-pinned"
    assert members[1] == "openrouter/deepseek/deepseek-v4"


def test_resolve_panel_respects_max_panel(fake_catalog):
    members = panel.resolve_panel(
        ["google", "qwen", "deepseek"], hints={"google": "flash"}, max_panel=2,
    )
    assert len(members) == 2


def test_resolve_panel_dedups(fake_catalog):
    members = panel.resolve_panel(
        ["google", "gemini"], hints={"google": "flash", "gemini": "flash"},
    )
    assert members == ["openrouter/google/gemini-3.5-flash"]


def test_resolve_judge():
    assert panel.resolve_judge("openrouter/anthropic/claude") == \
        "openrouter/anthropic/claude"
    assert panel.resolve_judge("") is None
    assert panel.resolve_judge("   ") is None


# ── inject_plugin (composes with provider-exclusion) ─────────────────────

def test_inject_plugin_preserves_provider_exclusion():
    kwargs = {"extra_body": {"provider": {"ignore": ["Stealth"]}}}
    inject_plugin(kwargs, {"id": "fusion", "analysis_models": ["a", "b"]})
    eb = kwargs["extra_body"]
    assert eb["provider"] == {"ignore": ["Stealth"]}  # untouched
    assert eb["plugins"] == [{"id": "fusion", "analysis_models": ["a", "b"]}]
    assert kwargs["tool_choice"] == "required"


def test_inject_plugin_appends_to_existing_plugins():
    kwargs = {"extra_body": {"plugins": [{"id": "other"}]}}
    inject_plugin(kwargs, {"id": "fusion"})
    assert [p["id"] for p in kwargs["extra_body"]["plugins"]] == ["other", "fusion"]


def test_inject_plugin_does_not_clobber_tool_choice():
    kwargs = {"tool_choice": "auto"}
    inject_plugin(kwargs, {"id": "fusion"})
    assert kwargs["tool_choice"] == "auto"


# ── budget cap ───────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(budget, "_FILE", tmp_path / "daily_spend.json")
    return tmp_path


def test_budget_records_and_caps(tmp_budget):
    assert budget.spent_today() == 0.0
    budget.record_spend(1.0)
    budget.record_spend(0.5)
    assert budget.spent_today() == pytest.approx(1.5)
    assert budget.under_cap(10.0) is True
    budget.record_spend(9.0)
    assert budget.under_cap(10.0) is False


def test_budget_zero_cap_is_unlimited(tmp_budget):
    budget.record_spend(999.0)
    assert budget.under_cap(0) is True


def test_budget_ignores_nonpositive(tmp_budget):
    budget.record_spend(-5.0)
    budget.record_spend(0.0)
    assert budget.spent_today() == 0.0


def test_budget_rolls_over_at_utc_midnight(tmp_budget, monkeypatch):
    budget.record_spend(5.0)
    assert budget.spent_today() == pytest.approx(5.0)
    monkeypatch.setattr(budget, "_today", lambda: "1999-01-01")
    assert budget.spent_today() == 0.0  # different day ⇒ fresh counter


# ── plan_for_role gating matrix ──────────────────────────────────────────

def _happy_config(monkeypatch, members, *, judge="", roles=("research",)):
    monkeypatch.setattr(config, "is_enabled_for", lambda role: role in roles)
    monkeypatch.setattr(config, "brake_engaged", lambda: False)
    monkeypatch.setattr(config, "daily_cap_usd", lambda: 10.0)
    monkeypatch.setattr(config, "panel_classes", lambda: ["google", "deepseek"])
    monkeypatch.setattr(config, "panel_pins", lambda: {})
    monkeypatch.setattr(config, "variant_hints", lambda: {})
    monkeypatch.setattr(config, "max_panel", lambda: 4)
    monkeypatch.setattr(config, "blocked_models", lambda: set())
    monkeypatch.setattr(config, "judge_id", lambda: judge)
    monkeypatch.setattr(panel, "resolve_panel", lambda **kw: list(members))
    monkeypatch.setattr(budget, "under_cap", lambda cap: True)
    monkeypatch.setattr(budget, "record_spend", lambda est: None)


def test_plan_disabled_returns_none(monkeypatch):
    _happy_config(monkeypatch, ["a", "b", "c"])
    monkeypatch.setattr(config, "is_enabled_for", lambda role: False)
    assert plan_for_role("research") == (None, 1)


def test_plan_out_of_scope_returns_none(monkeypatch):
    _happy_config(monkeypatch, ["a", "b", "c"], roles=("writing",))
    assert plan_for_role("research") == (None, 1)


def test_plan_brake_engaged_returns_none(monkeypatch):
    _happy_config(monkeypatch, ["a", "b", "c"])
    monkeypatch.setattr(config, "brake_engaged", lambda: True)
    assert plan_for_role("research") == (None, 1)


def test_plan_over_cap_returns_none(monkeypatch):
    _happy_config(monkeypatch, ["a", "b", "c"])
    monkeypatch.setattr(budget, "under_cap", lambda cap: False)
    assert plan_for_role("research") == (None, 1)


def test_plan_too_few_panel_returns_none(monkeypatch):
    _happy_config(monkeypatch, ["only-one"])
    assert plan_for_role("research") == (None, 1)


def test_plan_happy_builds_plugin_and_factor(monkeypatch):
    members = ["m1", "m2", "m3"]
    _happy_config(monkeypatch, members)
    plugin, factor = plan_for_role("research", max_tokens=1000,
                                   cost_in=1.0, cost_out=2.0)
    assert plugin == {"id": "fusion", "analysis_models": members}
    assert factor == len(members) + 1  # panel + judge


def test_request_scoped_force_enables_only_named_role(monkeypatch):
    _happy_config(monkeypatch, ["a", "b", "c"])
    monkeypatch.setattr(config, "is_enabled_for", lambda _role: False)
    monkeypatch.setattr(
        panel,
        "resolve_panel",
        lambda **kwargs: ["a", "b", "c"][:kwargs["max_panel"]],
    )

    with config.force_for_roles({"vetting"}, max_panel=2):
        plugin, factor = plan_for_role("vetting")
        assert plugin["analysis_models"] == ["a", "b"]
        assert factor == 3
        assert plan_for_role("research") == (None, 1)

    assert plan_for_role("vetting") == (None, 1)


def test_plan_happy_includes_judge_when_set(monkeypatch):
    _happy_config(monkeypatch, ["m1", "m2"], judge="openrouter/anthropic/claude")
    plugin, _ = plan_for_role("research")
    assert plugin["model"] == "openrouter/anthropic/claude"


def test_plan_does_not_record_at_plan_time(monkeypatch):
    # Metering moved to observe.record_response (ACTUAL response_cost) — the
    # naive panel_size+1 estimate under-counted the real ~10-20× fusion cost.
    recorded = []
    _happy_config(monkeypatch, ["m1", "m2"])
    monkeypatch.setattr(budget, "record_spend", lambda est: recorded.append(est))
    plan_for_role("research", max_tokens=1000, cost_in=1.0, cost_out=2.0)
    assert recorded == []


def test_plan_never_raises(monkeypatch):
    # A config that throws must degrade to a single-model call, not crash.
    def boom():
        raise RuntimeError("settings unavailable")
    monkeypatch.setattr(config, "is_enabled_for", lambda role: (_ for _ in ()).throw(RuntimeError()))
    assert plan_for_role("research") == (None, 1)


# ── fusion_state (operator snapshot) ─────────────────────────────────────

def test_fusion_state_shape(monkeypatch, fake_catalog):
    monkeypatch.setattr(config, "enabled", lambda: True)
    monkeypatch.setattr(config, "scope_roles", lambda: ["research"])
    monkeypatch.setattr(config, "panel_classes", lambda: ["google", "deepseek"])
    monkeypatch.setattr(config, "panel_pins", lambda: {})
    monkeypatch.setattr(config, "variant_hints", lambda: {"google": "flash"})
    monkeypatch.setattr(config, "max_panel", lambda: 4)
    monkeypatch.setattr(config, "blocked_models", lambda: set())
    monkeypatch.setattr(config, "judge_id", lambda: "")
    monkeypatch.setattr(config, "daily_cap_usd", lambda: 10.0)
    monkeypatch.setattr(config, "brake_engaged", lambda: False)
    monkeypatch.setattr(budget, "spent_today", lambda: 0.0)

    state = fusion_state()
    assert state["enabled"] is True
    assert state["active"] is True
    assert state["scope_roles"] == ["research"]
    assert [p["model_id"] for p in state["panel"]] == [
        "openrouter/google/gemini-3.5-flash",
        "openrouter/deepseek/deepseek-v4",
    ]
    assert state["cost_multiplier"] == 3  # 2 panel + judge
    assert state["judge"] == "(OpenRouter default)"


# ── observe: deliberation capture (Phase 2) ──────────────────────────────

@pytest.fixture()
def tmp_observe(monkeypatch, tmp_path):
    monkeypatch.setattr(observe, "_FILE", tmp_path / "deliberations.jsonl")
    return tmp_path


class _FakeMsg:
    def __init__(self, content):
        self.content = content
        self.annotations = None
        self.tool_calls = None
        self.reasoning = None


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeResp:
    def __init__(self, content, model="openrouter/x/y", extra=None, hidden=None):
        self.choices = [_FakeChoice(content)]
        self._extra = extra or {}
        self._model = model
        self._hidden_params = hidden or {}

    def model_dump(self):
        d = {"model": self._model}
        d.update(self._extra)
        return d


def test_observe_records_and_reads_back(tmp_observe):
    plugin = {"id": "fusion", "analysis_models": ["m1", "m2"], "model": "judge1"}
    resp = _FakeResp("the synthesized answer", extra={"router": {"type": "fusion"}})
    observe.record_response("research", plugin, resp)
    rows = observe.recent_deliberations(10)
    assert len(rows) == 1
    r = rows[0]
    assert r["role"] == "research"
    assert r["panel"] == ["m1", "m2"]
    assert r["judge"] == "judge1"
    assert r["answer_preview"].startswith("the synthesized")
    assert r["router"] == {"type": "fusion"}


def test_observe_newest_first_and_cap(tmp_observe, monkeypatch):
    monkeypatch.setattr(observe, "_CAP", 3)
    for i in range(5):
        observe.record_response("r", {"analysis_models": ["a", "b"]}, _FakeResp(f"ans{i}"))
    rows = observe.recent_deliberations(10)
    assert len(rows) == 3  # capped
    assert rows[0]["answer_preview"] == "ans4"  # newest first


def test_observe_failure_isolated(tmp_observe):
    class Boom:
        @property
        def choices(self):
            raise RuntimeError("boom")

    observe.record_response("r", {"analysis_models": ["a", "b"]}, Boom())  # must not raise
    assert observe.recent_deliberations(1)[0]["panel"] == ["a", "b"]


def test_recent_empty_when_no_file(tmp_observe):
    assert observe.recent_deliberations() == []


def test_observe_meters_actual_cost(tmp_observe, monkeypatch):
    recorded = []
    monkeypatch.setattr(budget, "record_spend", lambda c: recorded.append(c))
    resp = _FakeResp("ans", hidden={"response_cost": 0.0233})
    observe.record_response("research", {"analysis_models": ["a", "b"]}, resp)
    assert recorded == [0.0233]
    assert observe.recent_deliberations(1)[0]["response_cost"] == 0.0233


# ── agent_extra_body (Phase 2 agent-path) ────────────────────────────────

def _agent_cfg(monkeypatch, *, agent_on=True, in_scope=True, brake=False,
               members=("m1", "m2"), judge=""):
    monkeypatch.setattr(config, "agent_path_enabled", lambda: agent_on)
    monkeypatch.setattr(config, "is_enabled_for", lambda r: in_scope)
    monkeypatch.setattr(config, "brake_engaged", lambda: brake)
    monkeypatch.setattr(config, "panel_classes", lambda: ["google", "deepseek"])
    monkeypatch.setattr(config, "panel_pins", lambda: {})
    monkeypatch.setattr(config, "variant_hints", lambda: {})
    monkeypatch.setattr(config, "max_panel", lambda: 4)
    monkeypatch.setattr(config, "blocked_models", lambda: set())
    monkeypatch.setattr(config, "judge_id", lambda: judge)
    monkeypatch.setattr(panel, "resolve_panel", lambda **kw: list(members))


def test_agent_extra_body_disabled(monkeypatch):
    _agent_cfg(monkeypatch, agent_on=False)
    assert agent_extra_body("research") is None


def test_agent_extra_body_out_of_scope(monkeypatch):
    _agent_cfg(monkeypatch, in_scope=False)
    assert agent_extra_body("research") is None


def test_agent_extra_body_brake(monkeypatch):
    _agent_cfg(monkeypatch, brake=True)
    assert agent_extra_body("research") is None


def test_agent_extra_body_too_few(monkeypatch):
    _agent_cfg(monkeypatch, members=("only",))
    assert agent_extra_body("research") is None


def test_agent_extra_body_happy_offered_not_forced(monkeypatch):
    _agent_cfg(monkeypatch, members=("m1", "m2", "m3"), judge="judgeX")
    eb = agent_extra_body("research")
    assert eb == {
        "plugins": [
            {"id": "fusion", "analysis_models": ["m1", "m2", "m3"], "model": "judgeX"}
        ]
    }
    # Offered-not-forced: no tool_choice in the agent extra_body.
    assert "tool_choice" not in eb
