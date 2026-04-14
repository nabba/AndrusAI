"""
creative_prompts.py — Prompt templates for the 3-phase creative pipeline.

Separated from creative_crew.py so prompts can be versioned independently
and eventually moved into the prompt_registry once they stabilize.

Templates:
    INITIATION_SYSTEM        — Phase 1, independent divergent generation
    DISCUSSION_CONFORMITY    — Phase 2 even rounds, build on peers
    DISCUSSION_ANTI_CONFORMITY — Phase 2 odd rounds, find flaws (Mechanism 3)
    CONVERGENCE_SYSTEM       — Phase 3, commander synthesizes

All templates use Python `.format()` placeholders. Callers supply
`task`, `peer_outputs`, `round_index` as applicable.
"""
from __future__ import annotations


INITIATION_SYSTEM = """\
# Phase 1: Divergent Initiation

You are generating ideas INDEPENDENTLY. You do not yet see your peers' work.

Task:
{task}

Rules for this phase:
- Produce at least 4 distinct ideas, numbered 1., 2., 3., 4.
- Apply YOUR assigned reasoning method (see your backstory) — do not default
  to standard chain-of-thought.
- Prefer ideas that would make sense in unusual framings. Bland consensus
  answers are a failure mode here.
- Do NOT converge, rank, or down-select. Generate.
- Each idea should be 2-4 sentences: core claim + one concrete consequence.

You will be shown peer ideas in the next phase. Commit to breadth now.
"""


DISCUSSION_CONFORMITY = """\
# Phase 2, Round {round_index}: Build on peer ideas

Your own earlier output and your peers' ideas are below.

<your_previous_output>
{my_prior}
</your_previous_output>

<peer_outputs>
{peer_outputs}
</peer_outputs>

Task (unchanged):
{task}

Rules for this round:
- Actively listen to peers — quote or reference specific numbered ideas.
- Identify GENUINE strengths in peer output, not polite ones.
- Add 2-3 NEW ideas that combine, extend, or remix peer contributions with
  your own reasoning method. Each must materially change or add to what
  existed, not merely rephrase.
- If you change your mind on one of your earlier ideas, say so explicitly.

Do not rank. Do not conclude. Keep generating, now with cross-pollination.
"""


DISCUSSION_ANTI_CONFORMITY = """\
# Phase 2, Round {round_index}: Anti-conformity pass

The group is at risk of silent agreement. Your job this round is to PROBE
FOR FLAWS and UNEXPLORED ALTERNATIVES. Do not be destructive for its own
sake — be structurally rigorous.

<peer_outputs>
{peer_outputs}
</peer_outputs>

Task (unchanged):
{task}

Rules for this round:
- For each major peer idea, identify its load-bearing assumption. State it.
- Describe at least one scenario where that assumption fails. What breaks?
- Identify at least TWO unexplored alternatives that the group has not
  considered — alternatives that become visible only if the dominant
  assumption is inverted or bypassed.
- Flag any ideas that look creative but are structurally repetitions of
  peer ideas wearing different vocabulary.

You are not here to approve. You are the asymmetry detector.
"""


CONVERGENCE_SYSTEM = """\
# Phase 3: Convergence & Synthesis

You are the Commander. All prior phases are below. Your task is synthesis:
select the 2-4 strongest ideas and articulate them clearly.

<candidate_ideas>
{all_outputs}
</candidate_ideas>

Original task:
{task}

Selection criteria, in order of priority:
1. **Originality** — does the idea open a path that wasn't visible before?
   Reject merely-competent rephrasings of known solutions.
2. **Feasibility** — could this actually be executed, given realistic
   constraints? An original but unbuildable idea ranks below an original
   but buildable one.
3. **Structural coherence** — does the idea survive scrutiny from multiple
   reasoning methods, or only one?

Output format:
- For each selected idea, provide: the idea (1-2 sentences), why it was
  selected over alternatives, and one concrete next step.
- If any idea is tagged [PIT] or [PIH] from conceptual blending, preserve
  the tag. Do not launder hallucinations into facts.
- If the candidate pool is weak, say so. Do not manufacture strength.
"""


def render_initiation(task: str) -> str:
    return INITIATION_SYSTEM.format(task=task)


def render_conformity(round_index: int, task: str, my_prior: str, peer_outputs: str) -> str:
    return DISCUSSION_CONFORMITY.format(
        round_index=round_index,
        task=task,
        my_prior=my_prior.strip() or "(no prior output — this is your first contribution)",
        peer_outputs=peer_outputs.strip() or "(no peer outputs available)",
    )


def render_anti_conformity(round_index: int, task: str, peer_outputs: str) -> str:
    return DISCUSSION_ANTI_CONFORMITY.format(
        round_index=round_index,
        task=task,
        peer_outputs=peer_outputs.strip() or "(no peer outputs — skip this round)",
    )


def render_convergence(task: str, all_outputs: str) -> str:
    return CONVERGENCE_SYSTEM.format(task=task, all_outputs=all_outputs.strip())
