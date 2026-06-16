"""OpenRouter Fusion — server-side multi-model Mixture-of-Agents.

Fusion is a native OpenRouter feature (launched 2026-06-12): one completion
fans out to a *panel* of diverse models in parallel, a *judge* model compares
their answers (consensus / contradictions / unique insights / blind spots),
and a final synthesised answer comes back. We drive it by attaching a
``{"id": "fusion", "analysis_models": [...], "model": <judge>}`` plugin to the
``extra_body`` of a normal :func:`app.llm_factory.chat_completion_for_role`
call — the same passthrough channel ``_apply_openrouter_provider_exclusion``
already uses. The factory remains the sole LLM access path; OpenRouter owns the
parallelism, so there is no client-side fan-out to manage.

Design invariants:
  * Default OFF. Even with the master switch ON, nothing fuses until the
    operator adds roles to ``fusion_scope_roles`` from /cp/settings.
  * Role-scoped. Only raw completions whose ``role`` is in scope are fused.
  * OpenRouter only. Ollama (local) targets never reach the fusion hook.
  * No hardcoded model ids. "Model classes" (google / qwen / moonshotai /
    deepseek) resolve to the *current* OpenRouter champion per vendor from the
    live catalog at call time.
  * Fail-open. Any error in planning or injection degrades to a normal
    single-model completion.
  * Cost-capped. A per-day USD cap (separate from, and under, the monthly
    ceiling) plus the monthly-ceiling brake gate fusion before it spends.

This package imports ``app.runtime_settings`` *lazily* (inside functions),
mirroring the dispatcher's discipline, so the module stays import-clean and
unit-testable without the full settings/pydantic stack.
"""

from __future__ import annotations

from app.fusion.apply import (
    agent_extra_body,
    fusion_state,
    inject_plugin,
    plan_for_role,
)
from app.fusion.budget import record_spend, spent_today, under_cap
from app.fusion.observe import recent_deliberations, record_response

__all__ = [
    "plan_for_role",
    "inject_plugin",
    "agent_extra_body",
    "fusion_state",
    "record_response",
    "recent_deliberations",
    "record_spend",
    "spent_today",
    "under_cap",
]
