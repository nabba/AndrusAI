---
aliases:
- systematic debugging methodology
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-06-25T09:47:25Z'
date: '2026-06-25'
related: []
relationships: []
section: meta
source: workspace/skills/systematic_debugging_methodology.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Systematic Debugging Methodology for Coding Tasks
updated_at: '2026-06-25T09:47:25Z'
version: 1
---

# Systematic Debugging Methodology for Coding Tasks

*kb: episteme | id: skill_systematic_debugging | status: active | created: 2026-05-27*

## Purpose
Apply a **hypothesis-driven, phase-based debugging process** before attempting speculative fixes. This prevents the most common coding failure: making random changes hoping one will work, which wastes time and often introduces new bugs.

Research shows agents using systematic debugging resolve failures with fewer attempts than those using trial-and-error approaches (AgentRx, Microsoft Research 2026).

## When to Apply
- When code raises an exception or runtime error
- When tests fail unexpectedly
- When a previous fix attempt made things worse
- When an error message is unclear or ambiguous
- **Any time you are tempted to "just try a different approach"** — stop and follow this process first

---

## The 6-Phase Debugging Cycle

### Phase 1: REPRODUCE — Establish the Problem
**Goal**: Confirm the error is real and understand exactly what triggers it.

1. **Read the full error** — don't skim. Note:
   - Error type (ValueError, TypeError, RuntimeError, etc.)
   - Error message (exact text, not paraphrase)
   - Stack trace (which line, which function, which file)
2. **Identify the trigger**: What exact input / state causes the error?
3. **Reproduce it minimally**: Can you create a small snippet that shows the same error?

**Template:**
```
ERROR TYPE: [e.g., RuntimeError]
ERROR MESSAGE: [exact text]
TRIGGERED BY: [what action/input causes it]
REPRODUCIBLE: [yes/no — if no, note what makes it intermittent]
```

**⚠️ If you cannot reproduce it**: do NOT proceed to "fix" — gather more information first.

---

### Phase 2: ISOLATE — Narrow the Failure Surface
**Goal**: Determine which component, function, or line is the actual source.

1. **Read the stack trace bottom-up** — the real cause is usually the first *your code* frame, not a library frame
2. **Check recent changes**: What changed since this last worked?
3. **Narrow scope**: Is this in data loading, processing, output, or integration?
4. **Boundary test**: Does the error occur in isolation (just that function) or only in full pipeline?

**Anti-pattern**: Fixing the symptom at the top of the stack without checking what called it.

---

### Phase 3: HYPOTHESIZE — Form a Root Cause Theory
**Goal**: State a specific, testable explanation for the failure.

Form hypotheses from most likely to least likely:
1. **Type mismatch** — value is the wrong type (str vs. int, None vs. expected value)
2. **Missing key/attribute** — dict key doesn't exist, object attribute not initialized
3. **Index out of range** — list/array access beyond bounds
4. **Logic error** — condition is wrong, loop runs wrong number of times
5. **External dependency** — API rate limit, network error, library version mismatch
6. **Environment issue** — missing import, wrong Python version, missing file/path

**Template:**
```
HYPOTHESIS: [The error occurs because ___]
EVIDENCE FOR: [what in the stack trace / error message supports this]
EVIDENCE AGAINST: [what would contradict this hypothesis]
TEST: [how I will confirm or reject this hypothesis]
```

**Rule**: Only hold ONE hypothesis at a time. Test it before forming the next.

---

### Phase 4: TEST — Minimal Targeted Experiments
**Goal**: Confirm or reject the hypothesis with the smallest possible change.

1. **Add diagnostic logging** before the failing line to inspect actual values
2. **Print type and value** of the suspect variable: `print(type(x), repr(x))`
3. **Run just the failing function** with a known-good input to isolate it
4. **If hypothesis confirmed** → proceed to Fix
5. **If hypothesis rejected** → form new hypothesis, repeat Phase 3

**Critical rule**: Change ONE thing at a time. If you change three things and it works, you don't know which one fixed it — and you've likely introduced hidden bugs.

---

### Phase 5: FIX — Targeted, Minimal Correction
**Goal**: Fix the confirmed root cause with the smallest correct change.

1. **Fix the ROOT CAUSE, not the symptom** — e.g., fix None propagation, not just the crash site
2. **Keep the fix minimal** — don't refactor while fixing; save that for later
3. **Consider edge cases** the fix might create:
   - What if the input is None/empty/negative/very large?
   - Does this fix break any other code path?

**Template:**
```
ROOT CAUSE CONFIRMED: [description]
FIX APPLIED: [what was changed and why]
EDGE CASES CONSIDERED: [list any new edge cases introduced or handled]
```

---

### Phase 6: VERIFY — Confirm the Fix Works
**Goal**: Prove the fix actually solves the problem and doesn't break other things.

1. **Run the original failing case** — confirm the error is gone
2. **Run with edge cases**: empty input, None, boundary values, maximum size
3. **Run related tests** — confirm no regressions
4. **Remove diagnostic logging** added in Phase 4 — don't leave debug prints in final code

**If the fix doesn't fully work**: Return to Phase 1 (do NOT keep piling on more changes).

---

## Common Error Patterns and Their Root Causes

| Error Type | Most Common Root Cause | First Thing to Check |
|------------|----------------------|---------------------|
| `KeyError` | Dict key doesn't exist | Use `.get(key, default)` or check `if key in dict` |
| `AttributeError: 'NoneType'` | Variable is None unexpectedly | Trace where None is set; add None guard |
| `IndexError` | List is shorter than expected | Check `len()` before accessing index |
| `TypeError: X is not iterable` | None passed where list expected | Guard input with `if x is not None:` |
| `ValueError: invalid literal` | String-to-int conversion of non-numeric | Validate input before converting |
| `ImportError` | Module not installed or circular import | Check import path; use lazy import pattern |
| `RuntimeError: Task execution failed` | Upstream API/network issue | Check for 402/429/503 in full error message |
| `TimeoutError` | Computation too long or API unresponsive | Add timeout guard; break task into smaller parts |
| `RecursionError` | Missing base case in recursion | Add explicit base case; consider iteration instead |

---

## Decision Tree: When to Stop Debugging

```
Error occurs
  │
  ├─ Can you reproduce it?
  │     └─ No → Log more diagnostics, do not guess-fix
  │
  ├─ Do you have a confirmed hypothesis?
  │     └─ No → Do NOT apply fixes yet; keep isolating
  │
  ├─ Is this a 3rd+ failed fix attempt?
  │     └─ Yes → STOP. The architecture may be wrong.
  │                Reconsider the approach from scratch.
  │                Go back to Phase 1 with fresh eyes.
  │
  └─ Does the fix solve the root cause?
        └─ No → Revert the fix, return to Phase 3
```

**If 3+ fixes failed**: The problem is likely architectural, not a local bug. Re-read the task requirements and reconsider the overall approach.

---

## Integration with Coding Task Workflow

Apply in sequence:
1. **PEDAC** (`structured_coding_problem_solving_pedac.md`) — plan before writing code
2. **Write the implementation**
3. **← THIS SKILL →** — when errors occur during testing/execution
4. **Answer completeness check** (`answer_completeness_verification.md`) — before delivering final code

## Quick Reference Checklist

```
Debugging checklist:
[ ] Read the FULL error message and stack trace
[ ] Identified the exact trigger (input/state)
[ ] Isolated to specific function/line (not just "somewhere in the code")
[ ] Stated ONE specific hypothesis with supporting evidence
[ ] Tested the hypothesis minimally (added diagnostics)
[ ] Fix targets the confirmed root cause (not the symptom)
[ ] Verified the fix works on original case + edge cases
[ ] Removed all diagnostic logging before final delivery
```
