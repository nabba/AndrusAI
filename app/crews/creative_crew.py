"""
creative_crew.py — 3-phase divergent-discussion-convergence orchestrator.

Implements Mechanisms 1-5 from the creativity synthesis:
  M1 — diverse reasoning methods per agent (via compose_backstory)
  M2 — three-phase pipeline (Initiation → Discussion → Convergence)
  M3 — anti-conformity rounds (alternating with conformity)
  M4 — heterogeneous model tiers across agents in a single run
  M5 — phase-dependent sampling parameters

Also implements research-synthesis additions:
  GS-3 — alternation telemetry (associative distance density per phase)
  P4  — failure-mode scanning on final output

Design choices (per user decisions):
  - Custom orchestrator, NOT CrewAI Process.hierarchical. Each phase is a
    plain sequential Crew or a parallel fan-out.
  - Budget cap via app.creative_mode.get_budget_usd() — dashboard-adjustable.
    Runs abort mid-phase when exceeded, returning best-so-far output.
  - Commander synthesizes in Phase 3 as the agent of a sequential Task.
    No manager_llm, no hierarchical delegation.

Safety invariant preservation:
  - Torrance scoring (app.personality.creativity_scoring) runs at
    infrastructure level, not inside agent-modifiable prompts.
  - Anti-conformity is prompt-level, executed by the Critic agent which is
    not self-modifying.
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Callable

from crewai import Agent, Crew, Process, Task

from app.benchmarks import record_metric
from app.crews.creative_prompts import (
    render_anti_conformity,
    render_conformity,
    render_convergence,
    render_initiation,
)
from app.firebase_reporter import crew_completed, crew_failed, crew_started
from app.memory.belief_state import update_belief
from app.rate_throttle import get_active_tracker
from app.sanitize import wrap_user_input
from app.souls.loader import compose_backstory

logger = logging.getLogger(__name__)


# Reasoning method assignment — synthesis §3, Mechanism 1.
# Tunable; these reflect the defaults argued in the plan.
_REASONING_METHOD_BY_ROLE = {
    "commander":    "meta_reasoning",
    "researcher":   "step_back",
    "coder":        "compositional_cot",
    "writer":       "analogical_blending",
    "critic":       "contrastive",
    "introspector": "contrastive",  # Critic and Self-Improver share contrastive
}

# Heterogeneous tier mixing — M4. One model family per agent.
# "local" = Ollama (wild divergent generator), "budget" = DeepSeek via OR,
# "mid" = MiniMax, "premium" = Anthropic Sonnet (converger).
_TIER_BY_ROLE_CREATIVE = {
    "researcher": "local",    # divergent idea firehose
    "writer":     "mid",      # analogical synthesizer
    "coder":      "budget",   # compositional rigor, cheap
    "critic":     "premium",  # anti-conformity needs teeth
    "commander":  "premium",  # convergence must be coherent
}


@dataclass
class PhaseOutput:
    role: str
    text: str
    duration_s: float


@dataclass
class CreativeRunResult:
    final_output: str
    phase_1_outputs: list[PhaseOutput] = field(default_factory=list)
    phase_2_outputs: list[PhaseOutput] = field(default_factory=list)
    aborted_reason: str | None = None
    cost_usd: float = 0.0
    scores: dict | None = None


class BudgetExceeded(RuntimeError):
    """Raised internally when the creative budget cap is hit mid-run."""


def _check_budget(budget_usd: float, phase: str) -> None:
    tracker = get_active_tracker()
    if tracker is None:
        return
    if tracker.total_cost_usd > budget_usd:
        raise BudgetExceeded(
            f"creative_crew: budget ${budget_usd:.2f} exceeded in phase {phase} "
            f"(actual ${tracker.total_cost_usd:.4f}) — aborting"
        )


def _make_agent(role: str, reasoning_method: str, phase: str, create_fn: Callable) -> Agent:
    """Build a creative-mode agent: custom backstory + phase-tuned LLM.

    `create_fn` is the role's existing factory (create_researcher,
    create_writer, etc.). We construct a fresh Agent rather than mutating
    the factory's Agent to keep the factory's tool wiring untouched.
    """
    from app.llm_factory import create_specialist_llm

    # The factory has already configured tools for the role. Reuse by
    # instantiating once at default tier, extracting the tools, then
    # building a new Agent with our backstory + phase-tuned LLM.
    base = create_fn()  # default config — we only want its tools
    tools = list(getattr(base, "tools", []) or [])

    role_llm_name = _llm_role_name(role)
    tier = _TIER_BY_ROLE_CREATIVE.get(role, "mid")
    llm = create_specialist_llm(
        max_tokens=4096,
        role=role_llm_name,
        force_tier=tier,
        phase=phase,
    )

    backstory = compose_backstory(role, reasoning_method=reasoning_method)
    return Agent(
        role=base.role,
        goal=base.goal,
        backstory=backstory,
        llm=llm,
        tools=tools,
        max_execution_time=300,
        verbose=True,
    )


def _llm_role_name(agent_role: str) -> str:
    """Map agent module name to llm_selector role string."""
    return {
        "researcher":   "research",
        "writer":       "writing",
        "coder":        "coding",
        "critic":       "critic",
        "commander":    "commander",
        "introspector": "introspector",
    }.get(agent_role, "default")


def _run_single_task(
    agent: Agent,
    description: str,
    expected_output: str,
) -> str:
    """Run one agent on one task, returning the string output."""
    task = Task(description=description, expected_output=expected_output, agent=agent)
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    return str(crew.kickoff())


def _format_peers(outputs: list[PhaseOutput], exclude_role: str | None = None) -> str:
    lines = []
    for o in outputs:
        if exclude_role and o.role == exclude_role:
            continue
        lines.append(f"### {o.role}\n{o.text}")
    return "\n\n".join(lines)


# ── Phase implementations ──────────────────────────────────────────────────

def _phase_initiation(task_description: str, budget_usd: float) -> list[PhaseOutput]:
    """Phase 1: four agents generate ideas independently with reasoning-method
    diversity and heterogeneous tiers. Parallelism is optional — the
    existing parallel_runner is crew-granular, so we run sequentially here
    to keep cost tracking simple. (Parallelization is a follow-up.)
    """
    from app.agents.researcher import create_researcher
    from app.agents.writer import create_writer
    from app.agents.coder import create_coder
    from app.agents.critic import create_critic

    roster = [
        ("researcher", create_researcher),
        ("writer", create_writer),
        ("coder", create_coder),
        ("critic", create_critic),
    ]

    outputs: list[PhaseOutput] = []
    prompt = render_initiation(wrap_user_input(task_description))
    for role, factory in roster:
        _check_budget(budget_usd, f"initiation/{role}")
        start = _time.monotonic()
        method = _REASONING_METHOD_BY_ROLE.get(role, "meta_reasoning")
        try:
            agent = _make_agent(role, method, phase="diverge", create_fn=factory)
            text = _run_single_task(
                agent,
                description=prompt,
                expected_output="A numbered list of 4+ distinct ideas, each 2-4 sentences.",
            )
            outputs.append(PhaseOutput(role=role, text=text, duration_s=_time.monotonic() - start))
        except BudgetExceeded:
            raise
        except Exception as exc:
            logger.warning(f"creative_crew: {role} failed in initiation: {exc}")
            outputs.append(PhaseOutput(role=role, text=f"(error: {exc})", duration_s=_time.monotonic() - start))
    return outputs


def _phase_discussion(
    task_description: str,
    phase1: list[PhaseOutput],
    budget_usd: float,
    rounds: int = 2,
) -> list[PhaseOutput]:
    """Phase 2: alternating conformity (build) and anti-conformity (probe)
    rounds. Round 1 = conformity with original roster; round 2 = anti-
    conformity executed by the critic."""
    from app.agents.researcher import create_researcher
    from app.agents.writer import create_writer
    from app.agents.coder import create_coder
    from app.agents.critic import create_critic

    factories = {
        "researcher": create_researcher,
        "writer": create_writer,
        "coder": create_coder,
        "critic": create_critic,
    }

    all_discussion: list[PhaseOutput] = []
    rolling = list(phase1)  # current view of outputs, updated each round

    for r in range(1, rounds + 1):
        _check_budget(budget_usd, f"discussion/round-{r}")
        is_anti = (r % 2 == 0)  # even rounds = anti-conformity
        round_outputs: list[PhaseOutput] = []

        if is_anti:
            # Only the critic executes anti-conformity rounds
            role = "critic"
            start = _time.monotonic()
            peers_text = _format_peers(rolling)
            method = _REASONING_METHOD_BY_ROLE[role]
            try:
                agent = _make_agent(role, method, phase="discuss", create_fn=factories[role])
                prompt = render_anti_conformity(
                    round_index=r,
                    task=wrap_user_input(task_description),
                    peer_outputs=peers_text,
                )
                text = _run_single_task(
                    agent,
                    description=prompt,
                    expected_output="Flaw analysis + ≥2 unexplored alternatives.",
                )
                round_outputs.append(PhaseOutput(
                    role=f"{role} (anti-conformity r{r})",
                    text=text,
                    duration_s=_time.monotonic() - start,
                ))
            except BudgetExceeded:
                raise
            except Exception as exc:
                logger.warning(f"creative_crew: anti-conformity round {r} failed: {exc}")
        else:
            for role, factory in factories.items():
                if role == "critic":
                    continue  # critic waits for its anti-conformity round
                _check_budget(budget_usd, f"discussion/round-{r}/{role}")
                start = _time.monotonic()
                my_prior = next((o.text for o in phase1 if o.role == role), "")
                peers_text = _format_peers(rolling, exclude_role=role)
                method = _REASONING_METHOD_BY_ROLE.get(role, "meta_reasoning")
                try:
                    agent = _make_agent(role, method, phase="discuss", create_fn=factory)
                    prompt = render_conformity(
                        round_index=r,
                        task=wrap_user_input(task_description),
                        my_prior=my_prior,
                        peer_outputs=peers_text,
                    )
                    text = _run_single_task(
                        agent,
                        description=prompt,
                        expected_output="Cross-references + 2-3 new or revised ideas.",
                    )
                    round_outputs.append(PhaseOutput(
                        role=f"{role} (conformity r{r})",
                        text=text,
                        duration_s=_time.monotonic() - start,
                    ))
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    logger.warning(f"creative_crew: {role} failed in r{r}: {exc}")

        all_discussion.extend(round_outputs)
        rolling = rolling + round_outputs  # next round sees everything so far

    return all_discussion


def _phase_convergence(
    task_description: str,
    all_prior: list[PhaseOutput],
    budget_usd: float,
) -> PhaseOutput:
    """Phase 3: Commander synthesizes final output at low temperature.

    The existing commander is a class-based orchestrator, not an Agent
    factory. We build a dedicated synthesizer Agent here using the commander
    soul + meta-reasoning preamble + low-temp premium LLM. This keeps the
    commander's routing responsibility separate from its synthesis role.
    """
    _check_budget(budget_usd, "convergence")
    from app.llm_factory import create_specialist_llm

    start = _time.monotonic()
    llm = create_specialist_llm(
        max_tokens=4096,
        role="synthesis",
        force_tier="premium",
        phase="converge",
    )
    backstory = compose_backstory("commander", reasoning_method=_REASONING_METHOD_BY_ROLE["commander"])
    agent = Agent(
        role="Commander (Synthesizer)",
        goal="Synthesize candidate ideas into 2-4 strong, original, feasible proposals.",
        backstory=backstory,
        llm=llm,
        tools=[],
        max_execution_time=300,
        verbose=True,
    )
    prompt = render_convergence(
        task=wrap_user_input(task_description),
        all_outputs=_format_peers(all_prior),
    )
    text = _run_single_task(
        agent,
        description=prompt,
        expected_output="2-4 synthesized ideas with rationale and next steps.",
    )
    return PhaseOutput(role="commander (converge)", text=text, duration_s=_time.monotonic() - start)


# ── Public entry point ────────────────────────────────────────────────────

def run_creative_crew(
    task_description: str,
    creativity: str = "high",
    parent_task_id: str | None = None,
    discussion_rounds: int = 2,
) -> CreativeRunResult:
    """Run the full creative pipeline.

    Args:
        task_description: The user's request.
        creativity: "high" (full pipeline) or "medium" (skip anti-conformity).
                    "low" should not reach this function — commander routes
                    such tasks to standard crews.
        parent_task_id: Optional parent task for sub-agent tracking.
        discussion_rounds: Total discussion rounds (default 2 = 1 conformity
                           + 1 anti-conformity). Budget-degraded to 1 when
                           creativity == "medium".

    Returns:
        CreativeRunResult with final_output, per-phase traces, cost, scores.
    """
    from app.creative_mode import get_budget_usd
    budget_usd = get_budget_usd()

    # Budget-aware degradation (per user decision: auto-downgrade at low budget)
    if budget_usd < 0.05 and creativity == "high":
        logger.info(f"creative_crew: budget ${budget_usd:.2f} < $0.05 — downgrading to medium")
        creativity = "medium"
    if creativity == "medium":
        discussion_rounds = 1  # conformity only, skip anti-conformity

    task_id = crew_started(
        "creative",
        f"Creative: {task_description[:100]}",
        parent_task_id=parent_task_id,
        model="multi-tier",
    )
    update_belief("creative_crew", "working", current_task=task_description[:100])

    aborted_reason: str | None = None
    phase1: list[PhaseOutput] = []
    phase2: list[PhaseOutput] = []
    final: str = ""
    start = _time.monotonic()

    try:
        phase1 = _phase_initiation(task_description, budget_usd)
        phase2 = _phase_discussion(task_description, phase1, budget_usd, rounds=discussion_rounds)
        conv = _phase_convergence(task_description, phase1 + phase2, budget_usd)
        final = conv.text
    except BudgetExceeded as bx:
        aborted_reason = str(bx)
        logger.warning(aborted_reason)
        # Best-so-far fallback: use last available output.
        if phase2:
            final = phase2[-1].text
        elif phase1:
            final = "\n\n---\n\n".join(f"[{p.role}]\n{p.text}" for p in phase1)
        else:
            final = "(creative run aborted before any output was produced)"
    except Exception as exc:
        logger.exception(f"creative_crew: unhandled error: {exc}")
        aborted_reason = f"error: {exc}"
        crew_failed("creative", task_id, str(exc)[:200])
        update_belief("creative_crew", "failed", current_task=task_description[:100])
        raise

    duration = _time.monotonic() - start
    tracker = get_active_tracker()
    cost = tracker.total_cost_usd if tracker else 0.0
    tokens = tracker.total_tokens if tracker else 0
    models = ", ".join(sorted(tracker.models_used)) if (tracker and tracker.models_used) else ""

    # Torrance scoring (infrastructure-level — M7)
    scores: dict | None = None
    try:
        from app.personality.creativity_scoring import score_output
        scores = score_output(final, agent_role="creative_crew").as_dict()
    except Exception as exc:
        logger.debug(f"creative_crew: scoring unavailable: {exc}")

    # GS-3 alternation telemetry — associative distance density per phase
    try:
        from app.personality.creativity_scoring import extract_ideas, _safe_embed, _cosine_distance
        p1_ideas = []
        for o in phase1:
            p1_ideas.extend(extract_ideas(o.text))
        conv_ideas = extract_ideas(final)
        p1_density = _idea_diversity_density(p1_ideas)
        conv_density = _idea_diversity_density(conv_ideas)
        alternation_index = p1_density - conv_density  # positive = converged
        record_metric("creative_diverge_density", p1_density, {"creativity": creativity})
        record_metric("creative_converge_density", conv_density, {"creativity": creativity})
        record_metric("creative_alternation_index", alternation_index, {"creativity": creativity})
        if scores:
            scores["diverge_density"] = round(p1_density, 4)
            scores["converge_density"] = round(conv_density, 4)
            scores["alternation_index"] = round(alternation_index, 4)
    except Exception as exc:
        logger.debug(f"creative_crew: alternation telemetry failed: {exc}")

    # Failure-mode scan on final output (P4)
    try:
        from app.failure_modes import scan_for_failures
        failure_signals = scan_for_failures(task_description, final)
        if failure_signals:
            signal_names = [s.mode_name for s in failure_signals]
            record_metric("creative_failure_modes", len(failure_signals),
                          {"modes": ",".join(signal_names)})
            if scores:
                scores["failure_modes_detected"] = signal_names
    except Exception as exc:
        logger.debug(f"creative_crew: failure scan failed: {exc}")

    update_belief("creative_crew", "completed", current_task=task_description[:100])
    record_metric("creative_run_time", duration, {"creativity": creativity})
    record_metric("creative_run_cost", cost, {"creativity": creativity})
    crew_completed("creative", task_id, final[:2000],
                   tokens_used=tokens, model=models, cost_usd=cost)

    return CreativeRunResult(
        final_output=final,
        phase_1_outputs=phase1,
        phase_2_outputs=phase2,
        aborted_reason=aborted_reason,
        cost_usd=cost,
        scores=scores,
    )


def _idea_diversity_density(ideas: list[str]) -> float:
    """Mean pairwise cosine distance across ideas — higher = more diverse.

    This is the GS-3 "associative distance density" signature.
    Divergent phases should have high density; convergent should have low.
    """
    from app.personality.creativity_scoring import _safe_embed, _cosine_distance
    if len(ideas) < 2:
        return 0.0
    vecs = [v for v in (_safe_embed(idea) for idea in ideas[:20]) if v is not None]
    if len(vecs) < 2:
        return 0.0
    total = 0.0
    count = 0
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            total += _cosine_distance(vecs[i], vecs[j])
            count += 1
    return total / count if count > 0 else 0.0
