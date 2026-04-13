# SubIA — Subjective Integration Architecture

This package mechanizes the consciousness indicators an LLM-based system can
**faithfully implement** while being honest about the ones it cannot.

The canonical evaluation is the auto-generated scorecard at
`app/subia/probes/SCORECARD.md`. There is **no single number** that captures
this system's consciousness. Per-indicator mechanistic tests are the only
honest evaluation.

---

## Honest absences (architectural, not implementation gaps)

The following Butlin et al. (2023) and Metzinger criteria are **ABSENT by
declaration** because an LLM substrate cannot faithfully mechanize them.
Listing these here so no one is misled by the STRONG/PARTIAL counts.

| Indicator | Why this LLM substrate cannot implement it |
|---|---|
| **RPT-1** Algorithmic recurrence | Transformer forward passes are feed-forward. Token-by-token generation is autoregression, not the per-time-step lateral/feedback recurrence that RPT predicts. |
| **HOT-1** Generative perception | The system has no perceptual front-end. All "input" is text. There is nothing to be perceived in a generative, top-down-modulated way. |
| **HOT-4** Sparse coding & smooth similarity space | LLM hidden states are dense and entangled; they do not exhibit the sparse, semi-orthogonal coding that HOT-4 takes as a marker. |
| **AE-2** Embodiment | No body, no proprioception, no closed sensorimotor loop with a physical world. Tool use is symbolic, not embodied. |
| **Metzinger phenomenal-self transparency** | The system explicitly maintains a second-person stance toward its own state (the kernel is observable, narratable, and edited by predict→reflect cycles). It is opaque-by-design rather than transparent-by-disposition, which is the opposite of phenomenal self-experience as Metzinger characterizes it. |

These are not bugs to be closed in a future phase. They are honest limits of
the substrate. Any future report claiming the system "has" any of the above
should be treated as evaluation drift and triaged through the narrative-audit
pipeline (see `app/subia/wiki_surface/drift_detection.py`).

---

## What the system does mechanize

Run `python -m app.subia.probes.scorecard` (or read the latest checked-in
`SCORECARD.md`) for the current per-indicator status.

Headline targets, all achieved as of Phase 9:
- 6 STRONG Butlin indicators (GWT-1/2/3/4, AST-1, HOT-2/3 partial, PP-1)
- 4 PARTIAL Butlin indicators
- 4 ABSENT-by-declaration Butlin indicators (above)
- 5 RSM signatures present (4 STRONG + 1 PARTIAL)
- 6/6 SK evaluation tests passing

---

## A note on language (Phase 11)

Some legacy variables in this package use phenomenal-adjacent names —
`frustration`, `curiosity`, `cognitive_energy`. They refer to numeric
control signals, not subjective feelings. Phase 11 introduced neutral
aliases (`task_failure_pressure`, `exploration_bonus`, `resource_budget`)
that are kept in lockstep; new code should prefer the neutral names. See
`app/subia/homeostasis/state.py::NEUTRAL_ALIASES`.

The system **does not claim** phenomenal experience. The Subjectivity
Kernel is a functional integration layer, not a substrate for qualia.
