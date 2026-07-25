# One path, one contract — and where SubIA and creativity belong

**Date:** 2026-07-26 · **Status:** DESIGN NOTE, one change shipped
**Evidence:** `reports/GATE_DIAGNOSIS_2026-07-25.md` (read its Addendum 10 claims
ledger first) · **Supersedes the reasoning in:** `docs/ANSWERING_V2_PLAN.md` §1

---

## 1. The argument, from evidence rather than aesthetics

`ANSWERING_V2_PLAN` argued the crew topology caps *quality* — truncating merges,
blind writers. The 2026-07-25 investigation found something more concrete and more
damning:

**The forks do not differ in what the user wants. They differ in which safety
machinery happens to be bolted on.**

One golden-set run, one class of question, five different fates:

| fork | what it has | what it produced |
|---|---|---|
| `deep_research` | evidence gate, citation checks, critique | grounded report |
| plain `research` | **no gate at all** | raw tool-call syntax as a 79-char "report"; ReAct scratchpad; internal JSON |
| `pim` | neither | took the dossier request, crashed on a filename |
| `creative` | its own budget semantics | discarded a real poem on a budget bug |
| direct commander | nothing needed | answered the poem correctly |

And the fork is selected by a **non-deterministic LLM routing call plus a
non-deterministic difficulty score** — the same report questions went to
`deep_research` at 14:32 and plain `research` at 17:51; the poem scored difficulty
8 and then 2 the same day.

So the user-visible property is not "quality is capped" but **"quality is a
lottery over forks with different guarantees."**

The decisive tell is in this effort's own commits: **every gate fix protected
exactly one fork.** `[Sn]` precondition, closed citation set, evidence gate — all
`deep_research` only. When a fix must be applied N times and lands on one, the
forks are the bug.

## 2. But "one path" must not mean "one undifferentiated path"

Real differences that must survive any unification:

* **Latency budget** — "hey, how's it going" in seconds; a report in fifteen minutes.
* **Evidence requirement** — a poem needs no citations; a report needs resolvable ones.
* **Tool needs** — calendar needs the calendar; research needs the web.

Accidental differences, which are what should go: *which code path*, and
*which gates are attached to it*.

### The shape: one pipeline, parameterised by an output contract

The contract already exists. It was written on 2026-07-25 for the eval set
(`evals/golden_set.jsonl`): `intent`, `answer_shape`, `min_substance`,
`citation`, `must_address`, `degradation`, `ambiguity`.

Make that same object drive serving and four things stop being able to drift:

1. the pipeline knows whether retrieval is needed,
2. the gate knows what to check,
3. the eval knows how to score,
4. **a fix lands once, not per fork.**

That is the unification that matters — not "one code path" but **one contract,
honoured by one pipeline**. `app/crews/output_integrity.py` is the first concrete
instance: a single definition imported by both the serving gate and the scorer, so
a shape the gate suppresses and a shape the scorer fails cannot diverge.

### Keep the workflows that work

`company_dossier` and the `/delegate paper` factory are typed pipelines with real
value. Unification must not dissolve them into a generic agent — they become
**invokable stages** the one path can call. `ANSWERING_V2_PLAN` already said
"deterministic workflows stay workflows"; that part was right.

## 3. SubIA: influence, don't intercept

On 2026-07-25 SubIA was actively destructive — but from plumbing, not purpose. The
code contains its own indictment. `orchestrator._consume_pre_task_context`:

> *"Apply validated lifecycle context **without allowing task replacement**…
> SubIA uses a separate structured field, which is prepended independently and
> therefore cannot be silently discarded by the rewrite guard."*

A guard exists specifically to stop hooks replacing the task — and SubIA's
prepend route walked around it and replaced the task in effect. `crew_tasks`
topics read literally `--- SubIA Context --- loop: compressed scene (2 items…`
for at least two days. Fixed in `4c11f769` by teaching `_extract_core_topic` the
marker, but that is a patch on the symptom.

**The principle: SubIA must never touch the task string.** Its place is:

* **An observer** on the serving path — subscribe to events; record scene,
  beliefs, homeostatic state. It may legitimately influence *tone*, *model tier*,
  or raise a dispatch veto.
* **A typed side-channel** rather than concatenated text — the separate
  structured field its own docstring already claims it uses.
* **A retrieval tool** the writer or critic calls when self-knowledge is actually
  relevant ("what did we conclude about this last time?").

Every bit of self-model value survives. The ability to corrupt the input
disappears, structurally rather than by pattern-matching a marker.

## 4. Creativity: a stage, not a fork

Creativity should not compete with research and writing for a routing decision.
It is a **property of the output contract** — "this ask wants divergent ideation
/ novelty" — which switches on a diverge→discuss→converge **stage** inside the
one pipeline.

That change kills three 2026-07-25 bugs structurally rather than individually:

* no separate budget semantics to get wrong (one accounting, delta-measured —
  the bug that discarded a finished poem),
* no fork that can return a notification string as an answer,
* no possibility that a poem request misses the creative machinery because the
  router happened to score it difficulty 2.

And **Torrance scoring / anti-conformity are quality measurements** — they belong
in the verification stage beside the citation checks, where the rest of the system
can see them, not buried inside one crew's internals.

## 5. What shipped, and the experiment that should precede a rewrite

Given this effort's error rate — two outages, one shipped-then-reverted change,
two retracted stall diagnoses — the right move is evidence before restructuring.

**Shipped (`9e9112e7`):** an output-integrity gate applied to *every* crew, with
its definitions shared with the eval scorer. Deliberately not the evidence gate:
`ResearchCrew` is a plain `crew.kickoff()` returning a string and captures no
structured evidence set, so real groundedness checking for the non-deep path
requires building per-request evidence capture first. This establishes only that
a reply is an *answer* rather than internal machinery — which is exactly the
failure that occurred three times.

**The experiment that decides the rewrite:** re-run the golden set and check
whether outcomes are now *reproducible across runs*.

* If quality becomes consistent → the forks were the problem, and unification
  behind one contract is justified.
* If it stays inconsistent → the forks were not the ceiling, and a rewrite would
  have been expensive theatre.

That is a cheaper and sharper test than the original plan's "does v2 beat v1",
because it targets the mechanism this investigation actually found.

## 6. Honest position on `ANSWERING_V2_PLAN` Phases 2–4

The recommendation on 2026-07-25 morning was **hold** — the evidence did not
support topology as the quality ceiling. That still stands as written.

But tonight's evidence supports unification for a *different* reason:
**inconsistency**, not the quality ceiling the plan claimed. Different reason,
different first step. Unify the **contract and the gates** before touching the
agent loop, and let the reproducibility experiment above decide whether the loop
rewrite is needed at all.
