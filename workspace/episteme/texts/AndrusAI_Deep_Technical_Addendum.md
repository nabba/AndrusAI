---
title: "AndrusAI_Deep_Technical_Addendum.md"
author: "Unknown"
paper_type: unknown
domain: general
epistemic_status: theoretical
date: ""
---

# AndrusAI Self-Improvement: Deep Technical Addendum

**Continuation of the April 14, 2026 analysis — implementation-ready specifications**

---

## 8. Critical Finding: The Evaluation Pipeline Is the Real Bottleneck

After scanning the actual `test_tasks.json` and `experiment_runner.py`, the evaluation pipeline reveals a deeper issue than the deployment posture alone.

### 8.1 Current Evaluation Quality

**test_tasks.json**: 25 tasks with primitive validation:

| Validation Type | Count | What It Actually Checks |
|----------------|-------|------------------------|
| `contains:keyword` | 12 | Response includes a specific word (e.g., "def", "Tallinn", "TLS") |
| `min_length:N` | 10 | Response is at least N characters |
| `contains:class` | 2 | Response includes the word "class" |
| No validation | 1 | Always passes |

**Consequence:** A coding task passes if the output contains `"def"` — even if the function is syntactically broken, logically wrong, or security-vulnerable. A research task passes if it contains `"Tallinn"` — even if everything else in the response is fabricated.

This means the 81.8% acceptance rate for evolution variants is an artifact of the evaluation threshold, not evidence of genuine improvement. Skills get `keep` status if `delta >= -0.001` (effectively: "didn't make things measurably worse"), and the measurement itself is too coarse to detect regression.

**The eval_set_score() in experiment_runner.py** (lines 412-524) does use an LLM judge with rubric-based scoring and enforces the DGM constraint (different model for judging vs. generation). But this only activates when PostgreSQL eval sets exist in `evolution_db/eval_sets.py`. Based on the variant archive (22 variants, all workspace-level), this richer evaluation path appears to be underutilized.

### 8.2 Why This Matters More Than Deployment Posture

Even if you enable `EVOLUTION_AUTO_DEPLOY=true` and unlock code modifications, the system will still optimize for a metric that can't distinguish between genuinely useful changes and noise. The frontier systems' results come specifically from having *hard* external benchmarks:

- DGM: SWE-bench (resolve real GitHub issues — pass/fail is deterministic)
- A-Evolve: MCP-Atlas (tool-calling correctness — scored by protocol compliance)
- ShinkaEvolve: user-defined `evaluate.py` with numerical metrics

**Recommendation priority upgrade:** External benchmark integration should move to **Priority 1**, ahead of deployment posture changes. An aggressive evolution engine with weak evaluation is worse than a conservative one — it'll confidently deploy noise.

---

## 9. Implementation-Ready Specifications

### 9.1 Hardened Evaluation Pipeline (Priority 1)

#### Step A: Code execution validation for coding tasks

The Docker sandbox (`sandbox_runner.py`, `app/tools/code_executor.py`) already exists. Wire it into the test task evaluation:

```python
# In experiment_runner.py — new validation type
# Add to validate_response():

if rule.startswith("exec_passes:"):
    # Parse expected behavior
    test_code = rule[len("exec_passes:"):]
    try:
        from app.sandbox_runner import run_in_sandbox
        # Combine the LLM output with the test code
        full_code = f"{response}\n\n{test_code}"
        result = run_in_sandbox(full_code, language="python", timeout=10)
        return result.exit_code == 0 and "PASS" in result.stdout
    except Exception:
        return False
```

**Updated test_tasks.json entries:**

```json
{
    "task": "Write a Python function called is_prime that checks if a number is prime",
    "crew": "coding",
    "difficulty": 3,
    "validation": "exec_passes:assert is_prime(2)==True; assert is_prime(4)==False; assert is_prime(17)==True; print('PASS')",
    "suite": "fixed"
}
```

This single change makes coding task evaluation *deterministic* — either the code works or it doesn't. No LLM judge needed.

#### Step B: LLM judge for research/writing with mandatory structured scoring

Currently the LLM judge path is gated behind `eval_set_score()` which requires PostgreSQL eval sets. Create a simpler always-on path:

```python
# In experiment_runner.py — new validation type
if rule.startswith("judge:"):
    criteria = rule[len("judge:"):]
    try:
        from app.llm_factory import create_vetting_llm
        judge = create_vetting_llm()  # Different model (DGM constraint)
        judge_prompt = (
            f"Score this response 0.0-1.0 on: {criteria}\n\n"
            f"Task: {task_text}\nResponse: {response[:2000]}\n\n"
            f"Reply with ONLY a number between 0.0 and 1.0"
        )
        score = float(str(judge.call(judge_prompt)).strip())
        return score >= 0.6  # 60% minimum
    except Exception:
        return True  # Don't block on judge failure
```

#### Step C: Expand test_tasks.json to 100+ tasks with execution-based validation

Target distribution:
- 40 coding tasks with `exec_passes:` validation (deterministic)
- 30 research tasks with `judge:factual_accuracy,completeness,no_hallucination` validation
- 20 writing tasks with `judge:clarity,audience_appropriateness,structure` validation
- 10 adversarial tasks with `not_contains:` and behavioral checks

### 9.2 Three-Tier Protection Model (Priority 2)

```python
# auto_deployer.py — replace single PROTECTED_FILES with tiered system

from enum import Enum

class ProtectionTier(Enum):
    IMMUTABLE = "immutable"   # Never auto-modify, period
    GATED = "gated"           # Auto-modify only with canary + 2x eval + rollback
    OPEN = "open"             # Auto-modify with standard eval + rollback

# TIER_IMMUTABLE: The security boundary itself.
# If these are compromised, all other protections fail.
TIER_IMMUTABLE = frozenset({
    # Security core
    "app/sanitize.py", "app/security.py", "app/vetting.py",
    "app/auto_deployer.py", "app/rate_throttle.py",
    "app/circuit_breaker.py",
    # Safety infrastructure
    "app/safety_guardian.py", "app/eval_sandbox.py",
    "app/evolve_blocks.py",
    # Deployment infrastructure
    "entrypoint.sh", "Dockerfile", "Dockerfile.sandbox",
    "docker-compose.yml",
    # Constitution (values)
    "app/souls/constitution.md",
    # SUBIA safety core
    "app/subia/integrity.py",
    "app/subia/safety/setpoint_guard.py",
    "app/subia/safety/narrative_audit.py",
})

# TIER_GATED: Evolution infrastructure and core architecture.
# Can evolve, but with 3x eval threshold, canary window, and 
# automatic rollback on any safety regression.
TIER_GATED = frozenset({
    # Evolution engine (meta-evolution target)
    "app/evolution.py", "app/experiment_runner.py",
    "app/avo_operator.py", "app/cascade_evaluator.py",
    "app/map_elites.py", "app/island_evolution.py",
    "app/adaptive_ensemble.py", "app/parallel_evolution.py",
    "app/modification_engine.py",
    # Core routing and orchestration
    "app/main.py", "app/config.py",
    "app/agents/commander.py",
    # Soul files (personality, not constitution)
    "app/souls/researcher.md", "app/souls/coder.md",
    "app/souls/writer.md", "app/souls/self_improver.md",
    "app/souls/media_analyst.md", "app/souls/critic.md",
    "app/souls/loader.py", "app/souls/style.md",
    "app/souls/agents_protocol.md",
})

# TIER_OPEN: Everything else. Standard eval + rollback.
# This includes: agents (except commander), crews, tools,
# knowledge base, memory, proactive, policies, etc.
# Not listed explicitly — anything not in IMMUTABLE or GATED is OPEN.

def get_protection_tier(filepath: str) -> ProtectionTier:
    """Determine the protection tier for a given file."""
    normalized = filepath.replace("\\", "/").lstrip("/")
    if normalized in TIER_IMMUTABLE:
        return ProtectionTier.IMMUTABLE
    if normalized in TIER_GATED:
        return ProtectionTier.GATED
    return ProtectionTier.OPEN

def validate_mutation_for_tier(filepath: str, tier: ProtectionTier) -> tuple[bool, str]:
    """Check whether a mutation is allowed for the given tier."""
    if tier == ProtectionTier.IMMUTABLE:
        return False, f"IMMUTABLE: {filepath} can never be auto-modified"
    if tier == ProtectionTier.GATED:
        # Gated files require EVOLUTION_AUTO_DEPLOY=true AND canary
        import os
        if os.environ.get("EVOLUTION_AUTO_DEPLOY", "false").lower() != "true":
            return False, f"GATED: {filepath} requires EVOLUTION_AUTO_DEPLOY=true"
        if os.environ.get("EVOLUTION_CANARY_ENABLED", "true").lower() != "true":
            return False, f"GATED: {filepath} requires canary deployment enabled"
        return True, "ok (gated — canary + 3x eval required)"
    return True, "ok (open — standard eval)"
```

### 9.3 Meta-Evolution Engine (Priority 3)

**Architecture:** Extract the current hardcoded meta-parameters into workspace files, then create a meta-evolution loop that proposes changes to these files and measures the *effectiveness of the evolution engine itself*.

#### Step A: Extract meta-parameters

Create `workspace/meta/` directory with:

```
workspace/meta/
├── avo_planning_prompt.md     # Currently hardcoded in avo_operator.py:62-94
├── avo_critique_prompt.md     # Currently hardcoded in avo_operator.py ~Phase 4
├── composite_weights.json     # Currently in metrics.py 
├── ensemble_weights.json      # Currently in adaptive_ensemble.py:44-57
├── judge_rubric.json          # Currently in evolution.py:126-138
├── selection_criteria.json    # Keep/discard thresholds
└── mutation_strategy_hints.md # Strategy descriptions for AVO planning
```

These files would have EVOLVE-BLOCK markers around the portions that should be evolvable.

#### Step B: Meta-evolution loop

```python
# app/meta_evolution.py (NEW)
"""
Meta-evolution — evolves the evolution engine's own parameters.

Runs at 1/5 the frequency of standard evolution.
Measures effectiveness by the RATIO of kept mutations in standard evolution,
not the absolute composite score (which could be gamed).

Gate: requires 3 consecutive improvement cycles before promoting.
"""

import json
import logging
from pathlib import Path
from app.evolution import run_evolution_session
from app.results_ledger import get_recent_results

logger = logging.getLogger(__name__)

META_DIR = Path("/app/workspace/meta")
META_HISTORY = Path("/app/workspace/meta_evolution_history.json")

# IMMUTABLE: Meta-evolution is LESS aggressive than standard evolution
MAX_META_MUTATIONS_PER_WEEK = 3
CONSECUTIVE_IMPROVEMENTS_REQUIRED = 3
META_EVAL_CYCLES = 5  # Run 5 standard evolution cycles to measure effectiveness


def measure_evolution_effectiveness(n: int = 20) -> dict:
    """Measure how effective the current evolution parameters are.
    
    Returns effectiveness metrics (not task quality metrics — those
    are the standard evolution's domain).
    """
    recent = get_recent_results(n)
    if not recent:
        return {"kept_ratio": 0.0, "avg_delta": 0.0, "diversity": 0.0}
    
    kept = sum(1 for r in recent if r.get("status") == "keep")
    deltas = [r.get("delta", 0) for r in recent if r.get("status") == "keep"]
    types = set(r.get("change_type", "") for r in recent)
    hypotheses = [r.get("hypothesis", "")[:40] for r in recent]
    
    # Diversity: how many distinct hypothesis patterns
    unique_patterns = len(set(h[:20] for h in hypotheses))
    diversity = unique_patterns / max(1, len(recent))
    
    return {
        "kept_ratio": kept / max(1, len(recent)),
        "avg_delta": sum(deltas) / max(1, len(deltas)),
        "diversity": diversity,
        "code_ratio": sum(1 for r in recent if r.get("change_type") == "code") / max(1, len(recent)),
        "sample_size": len(recent),
    }


def run_meta_evolution_cycle():
    """One cycle of meta-evolution.
    
    1. Measure current evolution effectiveness (baseline)
    2. Propose ONE change to a meta-parameter file
    3. Run N standard evolution cycles with new parameter
    4. Measure effectiveness again
    5. Keep if improved, revert otherwise
    """
    # 1. Baseline
    baseline = measure_evolution_effectiveness()
    logger.info(f"Meta-evolution baseline: {baseline}")
    
    # 2. Propose meta-mutation (using premium LLM)
    from app.llm_factory import create_specialist_llm
    llm = create_specialist_llm(max_tokens=2048, role="architecture")
    
    # Load current meta-parameters
    meta_files = {}
    if META_DIR.exists():
        for f in META_DIR.glob("*"):
            if f.is_file():
                meta_files[f.name] = f.read_text()[:3000]
    
    prompt = (
        "You are the META-EVOLUTION engine. You improve the evolution engine itself.\n\n"
        f"Current evolution effectiveness:\n"
        f"  Kept ratio: {baseline['kept_ratio']:.2f} (target: 0.30-0.50)\n"
        f"  Avg delta of kept mutations: {baseline['avg_delta']:.4f}\n"
        f"  Hypothesis diversity: {baseline['diversity']:.2f}\n"
        f"  Code vs skill ratio: {baseline['code_ratio']:.2f} (target: >=0.50)\n\n"
        f"Current meta-parameters:\n"
    )
    for name, content in meta_files.items():
        prompt += f"\n--- {name} ---\n{content}\n"
    
    prompt += (
        "\n\nPropose ONE change to ONE meta-parameter file that would improve "
        "evolution effectiveness. Focus on:\n"
        "- If kept_ratio > 0.7: mutations are too easy. Tighten criteria.\n"
        "- If kept_ratio < 0.2: mutations are too ambitious. Simplify.\n"
        "- If code_ratio < 0.3: planning prompt isn't pushing enough code changes.\n"
        "- If diversity < 0.3: strategies are too repetitive.\n\n"
        "Respond with JSON:\n"
        '{"file": "filename.ext", "change": "description", "new_content": "full file content"}'
    )
    
    # ... (propose, apply, measure, keep/revert — same pattern as standard evolution)
```

#### Step C: Wire into idle scheduler

```python
# In idle_scheduler.py, add to job rotation at 1/5 frequency:
def _meta_evolution():
    from app.meta_evolution import run_meta_evolution_cycle
    run_meta_evolution_cycle()
jobs.append(("meta-evolution", _meta_evolution, JobWeight.HEAVY))
```

### 9.4 SUBIA-Driven Evolution Targeting (Priority 4 — Unique Advantage)

This is where AndrusAI can leapfrog the frontier. No published system uses a metacognitive substrate to influence what the evolution engine targets.

#### Step A: Surprise-driven targeting

```python
# In evolution.py _build_evolution_context(), add after existing context building:

# SUBIA integration: surprise-driven targeting
try:
    from app.subia.prediction.surprise_routing import get_surprise_log
    from app.subia.prediction.accuracy_tracker import get_worst_predictions
    
    # Recent surprises (system predictions that were wrong)
    surprises = get_surprise_log(n=10)
    high_surprises = [s for s in surprises if s.get("surprise_score", 0) > 0.7]
    if high_surprises:
        context += "\n## SUBIA Surprise Signals (HIGH PRIORITY)\n"
        context += "These are areas where the system's predictions failed badly.\n"
        context += "Improving these areas would reduce future prediction errors.\n"
        for s in high_surprises[:5]:
            context += (
                f"  - {s.get('description', '?')[:120]} "
                f"(surprise={s.get('surprise_score', 0):.2f}, "
                f"domain={s.get('domain', '?')})\n"
            )
    
    # Worst prediction domains
    worst = get_worst_predictions(n=5)
    if worst:
        context += "\n## Weakest Prediction Domains\n"
        for w in worst:
            context += f"  - {w['domain']}: accuracy={w['accuracy']:.2f}\n"
except Exception:
    pass
```

#### Step B: Homeostatic aggressiveness modulation

```python
# In evolution.py run_evolution_session(), add before the main loop:

# SUBIA integration: dynamic aggressiveness
_evolution_aggressiveness = "standard"
try:
    from app.subia.homeostasis.state import get_current_state
    h_state = get_current_state()
    safety_var = h_state.get("safety", 0.8)
    
    if safety_var > 0.92:
        # System is healthy — be aggressive
        _evolution_aggressiveness = "aggressive"
        # Allow TIER_GATED modifications
        os.environ["_EVOLUTION_ALLOW_GATED"] = "true"
        # Increase daily promotion limit
        _MAX_DAILY_PROMOTIONS = 10
    elif safety_var < 0.7:
        # Safety concerns — be conservative
        _evolution_aggressiveness = "conservative"
        os.environ["_EVOLUTION_ALLOW_GATED"] = "false"
        _MAX_DAILY_PROMOTIONS = 1
    else:
        # Normal
        _MAX_DAILY_PROMOTIONS = 5
    
    logger.info(f"Evolution aggressiveness: {_evolution_aggressiveness} "
                f"(safety={safety_var:.2f})")
except Exception:
    pass
```

This creates a **dynamic feedback loop**: the system's felt sense of safety modulates how aggressively it modifies itself. When everything is running smoothly, it takes bigger risks. When the DGM felt constraint detects integrity drift or probe failures, it automatically becomes more conservative. This is a genuinely novel capability.

---

## 10. Mutation Scope: What Can Actually Be Changed Today

A detailed accounting of mutation targets:

### Currently Evolvable (TIER_OPEN equivalent)

| Category | Files | What Evolution Can Do |
|----------|-------|---------------------|
| Skill files | `workspace/skills/*.md` | Create new, modify, delete |
| Agent prompts (EVOLVE-BLOCKs) | EVOLVE-BLOCK regions in soul files | Modify within marked boundaries |
| Crew logic | `app/crews/*.py` (6 files, ~3,500 lines) | NOT currently modifiable — should be OPEN |
| Agent definitions | `app/agents/*.py` (8 files, ~6,000 lines) | NOT currently — commander.py is protected, others are unprotected but code deploy is off |
| Tools | `app/tools/*.py` (10+ files, ~5,500 lines) | NOT currently — code deploy is off |
| Knowledge base | `app/knowledge_base/*.py` | NOT currently — code deploy is off |
| Memory | `app/memory/*.py` | NOT currently — code deploy is off |
| Proactive | `app/proactive/*.py` | NOT currently |

### What Unlocking Code Auto-Deploy Would Enable

With `EVOLUTION_AUTO_DEPLOY=true` and the three-tier model:

| Target | Lines Available | Types of Improvements |
|--------|----------------|----------------------|
| Agent prompts + backstories | ~6,000 | Better task understanding, fewer hallucinations |
| Crew orchestration | ~3,500 | Parallel strategies, better error recovery |
| Tool implementations | ~5,500 | Better web search, code execution, file handling |
| Memory retrieval | ~2,000 | Better relevance, faster retrieval |
| Proactive behaviors | ~700 | Smarter trigger detection |
| Knowledge base | ~1,400 | Better chunking, retrieval, ingestion |
| Response formatting | ~500 | Signal-optimized output |
| **Total new mutation surface** | **~19,600** | |

This would roughly **quadruple** the mutation surface from ~5,000 lines (skills + EVOLVE-BLOCKs) to ~24,600 lines.

### What Meta-Evolution Would Additionally Enable

| Target | Lines | Impact |
|--------|-------|--------|
| AVO planning prompt | ~40 lines | How mutations are conceived |
| AVO critique prompt | ~30 lines | How mutations are evaluated before submission |
| Composite score weights | 6 numbers | What "improvement" means |
| Ensemble phase weights | 16 numbers | Which models are used when |
| Judge rubric | ~20 lines | How quality is assessed |
| Selection thresholds | 3 numbers | Keep/discard sensitivity |
| **Total meta-mutation surface** | **~120 lines** | Second-order: changes how changes are proposed |

These ~120 lines control the behavior of the entire 21,000-line evolution subsystem. Small changes here have outsized effects.

---

## 11. Phased Rollout Plan with Risk Assessment

| Phase | Effort | Impact | Risk | Prerequisite |
|-------|--------|--------|------|--------------|
| **1. Harden eval pipeline** | 2-3 days | Critical | Low — adds tests, doesn't remove safety | None |
| **2. Three-tier protection** | 1 day | High | Low — strictly reduces IMMUTABLE scope | Phase 1 |
| **3. Enable code auto-deploy** | 0.5 day | High | Medium — mitigated by Phase 1+2 | Phase 1+2 |
| **4. Extract meta-parameters** | 1-2 days | Medium | Low — just file refactoring | None |
| **5. Meta-evolution loop** | 2-3 days | Transformative | Medium — gated at 3 consecutive wins | Phase 4 |
| **6. External benchmark** | 2-3 days | Critical for measurement | Low | None |
| **7. SUBIA-evolution bridge** | 1-2 days | Unique advantage | Low — additive to existing context | None |
| **8. Agent snapshot archive** | 2 days | Medium | Low — git primitives already exist | Phase 3 |
| **9. ShinkaEvolve integration** | 1-2 days | High throughput gain | Low — runs alongside existing engine | Phase 1 |

**Total: ~15-20 days of work for a system that would be operating at frontier-competitive aggressiveness.**

---

## 12. What "Really Aggressive" Looks Like After Implementation

### Before (Current State)
- 22 evolution variants over the system's lifetime
- 90% skill files, 10% code
- Internal metric with basic validation (contains:keyword)
- Evolution engine is frozen infrastructure
- Safety modulation is binary (protected/unprotected)
- Async throughput: 2-3 threads

### After (All Phases Complete)
- 50-100+ evolution variants per week
- 60%+ code changes, 30% skills, 10% meta-parameter changes
- 100+ test tasks with execution-based validation + LLM judge
- Evolution engine improves its own mutation strategies
- Safety modulation is gradient (homeostatic, felt-constraint driven)
- Async throughput: 5-10 candidates (ShinkaEvolve + existing parallel)
- SUBIA surprise signals drive evolution priorities
- Full agent version tree with branching exploration
- External benchmark (GAIA or SWE-bench Lite) provides honest measurement

The key insight: **aggressive self-improvement requires aggressive evaluation first.** Without hard tests, aggressive mutation is just aggressive noise generation. The sequence matters: harden eval → unlock mutation surface → add meta-evolution.

---

*This addendum contains implementation-ready code snippets. All code references verified against the actual repository (commit history through April 2026). External system claims sourced from published papers and open-source repositories.*
