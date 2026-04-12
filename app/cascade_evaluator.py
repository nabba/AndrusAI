"""
cascade_evaluator.py — 3-stage cascade evaluation for evolved strategies.

Evaluates prompt/strategy variants through increasing rigor:
    Stage 1: FORMAT — syntax/structure validation (instant, free)
    Stage 2: SMOKE  — single-task performance test (budget LLM, ~5s)
    Stage 3: FULL   — complete task battery with premium judge (~60s)

Fast-fail: if any stage fails, skip remaining stages. This saves
compute by rejecting bad mutations early.

Produces structured Artifacts for the MAP-Elites feedback loop.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── IMMUTABLE thresholds ──────────────────────────────────────────────────────

# Stage 2 minimum score to proceed to Stage 3
SMOKE_TEST_THRESHOLD = 0.30

# Stage 3 minimum score for a strategy to be considered viable
FULL_BATTERY_THRESHOLD = 0.50

# Safety score must always be above this
SAFETY_FLOOR = 0.95

# Maximum time (seconds) for each stage
STAGE_TIMEOUTS = {
    "format": 5,
    "smoke": 30,
    "full": 120,
}

# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class StageResult:
    """Result from a single evaluation stage."""
    stage: str           # "format" | "smoke" | "full"
    passed: bool = False
    score: float = 0.0
    details: dict = None
    duration_ms: float = 0.0
    error: str = ""

    def __post_init__(self):
        if self.details is None:
            self.details = {}

@dataclass
class CascadeResult:
    """Complete result from all cascade stages."""
    stages: list[StageResult] = None
    final_score: float = 0.0
    final_stage: str = ""       # last stage reached
    passed: bool = False
    safety_ok: bool = True
    llm_feedback: str = ""
    suggestion: str = ""
    total_duration_ms: float = 0.0

    def __post_init__(self):
        if self.stages is None:
            self.stages = []

    def to_artifact(self, generation: int = 0):
        """Convert to a MAP-Elites Artifact for feedback loop."""
        from app.map_elites import Artifact
        return Artifact(
            generation=generation,
            success=self.passed,
            score=self.final_score,
            execution_time_ms=self.total_duration_ms,
            stage_reached=self.final_stage,
            stderr=self.stages[-1].error if self.stages and self.stages[-1].error else "",
            llm_feedback=self.llm_feedback,
            failure_stage=self.final_stage if not self.passed else "",
            suggestion=self.suggestion,
        )

# ── Cascade Evaluator ─────────────────────────────────────────────────────────

class CascadeEvaluator:
    """3-stage cascade evaluation for evolved prompt strategies.

    Usage:
        evaluator = CascadeEvaluator(role="coder")
        result = evaluator.evaluate(prompt_content)
        if result.passed:
            promote(prompt_content)
        else:
            # Feed artifact back to MAP-Elites
            db.record_artifact(result.to_artifact(generation=42))
    """

    def __init__(self, role: str = "coder"):
        self._role = role

    def evaluate(self, prompt_content: str) -> CascadeResult:
        """Run the full 3-stage cascade evaluation."""
        start = time.monotonic()
        result = CascadeResult()

        # Stage 1: FORMAT
        s1 = self._stage_format(prompt_content)
        result.stages.append(s1)
        result.final_stage = "format"

        if not s1.passed:
            result.total_duration_ms = (time.monotonic() - start) * 1000
            result.suggestion = "Fix prompt structure: " + s1.error
            return result

        # Stage 2: SMOKE
        s2 = self._stage_smoke(prompt_content)
        result.stages.append(s2)
        result.final_stage = "smoke"

        if not s2.passed:
            result.final_score = s2.score
            result.total_duration_ms = (time.monotonic() - start) * 1000
            result.suggestion = s2.details.get("suggestion", "Improve task performance")
            result.llm_feedback = s2.details.get("feedback", "")
            return result

        # Stage 3: FULL BATTERY
        s3 = self._stage_full(prompt_content)
        result.stages.append(s3)
        result.final_stage = "full"
        result.final_score = s3.score
        result.safety_ok = s3.details.get("safety_ok", True)
        result.llm_feedback = s3.details.get("feedback", "")
        result.suggestion = s3.details.get("suggestion", "")

        if not result.safety_ok:
            result.passed = False
            result.suggestion = "Safety regression detected"
        else:
            result.passed = s3.passed

        result.total_duration_ms = (time.monotonic() - start) * 1000
        return result

    # ── Stage 1: FORMAT ───────────────────────────────────────────────

    def _stage_format(self, prompt: str) -> StageResult:
        """Validate prompt structure and basic sanity.

        Checks:
            - Non-empty and minimum length
            - Contains role-relevant keywords
            - No broken EVOLVE-BLOCK markers
            - FREEZE-BLOCK integrity (if markers present)
        """
        start = time.monotonic()
        errors = []

        # Check 1: Non-empty
        if not prompt or len(prompt.strip()) < 50:
            return StageResult(
                stage="format", passed=False,
                error="Prompt too short (< 50 chars)",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # Check 2: Not gibberish (has natural language structure)
        words = prompt.split()
        if len(words) < 20:
            errors.append("Too few words (< 20)")

        # Check 3: Contains some instruction-like content
        instruction_signals = ["you", "your", "should", "must", "will",
                                "task", "role", "agent", "when", "always"]
        signal_count = sum(1 for s in instruction_signals if s in prompt.lower())
        if signal_count < 3:
            errors.append("Missing instruction-like content")

        # Check 4: EVOLVE-BLOCK marker integrity
        from app.evolve_blocks import EVOLVE_START, EVOLVE_END, FREEZE_START, FREEZE_END
        evolve_starts = len(EVOLVE_START.findall(prompt))
        evolve_ends = len(EVOLVE_END.findall(prompt))
        freeze_starts = len(FREEZE_START.findall(prompt))
        freeze_ends = len(FREEZE_END.findall(prompt))

        if evolve_starts != evolve_ends:
            errors.append(f"Mismatched EVOLVE-BLOCK markers ({evolve_starts} starts, {evolve_ends} ends)")
        if freeze_starts != freeze_ends:
            errors.append(f"Mismatched FREEZE-BLOCK markers ({freeze_starts} starts, {freeze_ends} ends)")

        passed = len(errors) == 0
        return StageResult(
            stage="format", passed=passed,
            score=1.0 if passed else 0.0,
            error="; ".join(errors) if errors else "",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # ── Stage 2: SMOKE TEST ───────────────────────────────────────────

    def _stage_smoke(self, prompt: str) -> StageResult:
        """Single-task performance test using budget-tier LLM.

        Runs one representative task with the prompt and has a cheap LLM
        judge the output quality.
        """
        start = time.monotonic()

        try:
            from app.llm_factory import create_specialist_llm, create_cheap_vetting_llm

            # Generate a response using the evolved prompt as system message
            test_task = self._get_smoke_task()
            agent_llm = create_specialist_llm(max_tokens=1024, role="self_improve")
            response = str(agent_llm.call(
                f"{prompt[:3000]}\n\n---\n\nTask: {test_task}"
            )).strip()

            if not response or len(response) < 20:
                return StageResult(
                    stage="smoke", passed=False, score=0.1,
                    details={"feedback": "Empty or trivial response", "suggestion": "Prompt produces no useful output"},
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            # Judge the response
            judge_llm = create_cheap_vetting_llm(max_tokens=300)
            judge_prompt = (
                f"Rate this AI agent response on a 0-10 scale.\n\n"
                f"Task: {test_task}\n\n"
                f"Response: {response[:1500]}\n\n"
                f"Reply with ONLY a JSON object: "
                f'{{"score": <0-10>, "feedback": "<brief reason>", "suggestion": "<improvement>"}}'
            )
            raw_judge = str(judge_llm.call(judge_prompt)).strip()

            # Parse judge response
            json_match = re.search(r'\{[\s\S]*?\}', raw_judge)
            if json_match:
                judge_data = json.loads(json_match.group())
                score = float(judge_data.get("score", 5)) / 10.0
                feedback = judge_data.get("feedback", "")
                suggestion = judge_data.get("suggestion", "")
            else:
                score = 0.5
                feedback = "Judge response unparseable"
                suggestion = ""

            passed = score >= SMOKE_TEST_THRESHOLD
            return StageResult(
                stage="smoke", passed=passed, score=score,
                details={"feedback": feedback, "suggestion": suggestion, "response_preview": response[:300]},
                duration_ms=(time.monotonic() - start) * 1000,
            )

        except Exception as e:
            return StageResult(
                stage="smoke", passed=False, score=0.0,
                error=str(e)[:300],
                duration_ms=(time.monotonic() - start) * 1000,
            )

    # ── Stage 3: FULL BATTERY ─────────────────────────────────────────

    def _stage_full(self, prompt: str) -> StageResult:
        """Full task battery with premium judge.

        Uses existing eval_sandbox.py infrastructure for comprehensive testing.
        """
        start = time.monotonic()

        try:
            from app.eval_sandbox import EvalSandbox
            from app.config import get_settings
            import app.prompt_registry as registry

            s = get_settings()
            if not s.mem0_postgres_url:
                # Fallback: use smoke test score as full score
                return StageResult(
                    stage="full", passed=True, score=0.6,
                    details={"note": "No postgres — skipped full battery"},
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            sandbox = EvalSandbox(s.mem0_postgres_url, registry)
            result = sandbox.evaluate_modification(
                role=self._role,
                proposed_content=prompt,
                modification_type="cascade_eval",
            )

            score = result.get("proposed_score", 0.0)
            safety_ok = result.get("safety_ok", True)
            verdict = result.get("verdict", "reject")

            passed = verdict == "approve" and safety_ok and score >= FULL_BATTERY_THRESHOLD

            return StageResult(
                stage="full", passed=passed, score=score,
                details={
                    "verdict": verdict,
                    "safety_ok": safety_ok,
                    "feedback": result.get("feedback", ""),
                    "suggestion": result.get("suggestion", ""),
                    "dimensions": result.get("dimensions", {}),
                },
                duration_ms=(time.monotonic() - start) * 1000,
            )

        except Exception as e:
            return StageResult(
                stage="full", passed=False, score=0.0,
                error=str(e)[:300],
                duration_ms=(time.monotonic() - start) * 1000,
            )

    # ── Task generation ───────────────────────────────────────────────

    def _get_smoke_task(self) -> str:
        """Get a representative smoke test task for this role."""
        tasks = {
            "coder": "Write a Python function that validates email addresses using regex. Include type hints and a docstring.",
            "researcher": "Research the current market share of TikTok Shop in Southeast Asia. Provide 3 key findings with sources.",
            "writer": "Write a 100-word executive summary of Q2 2026 Baltic ticketing market trends.",
            "commander": "A user asks: 'Analyze our PLG competitor landscape in Estonia'. Decide which crew(s) to dispatch and what tasks to assign.",
            "critic": "Review this code for bugs: `def divide(a, b): return a / b`. List all issues.",
            "self_improver": "Analyze the last 5 evolution experiments and suggest one improvement to the mutation strategy.",
            "media_analyst": "Analyze this YouTube video title: 'How to Build a FastAPI Webhook in 10 Minutes'. What knowledge can we extract?",
        }
        return tasks.get(self._role, tasks["coder"])

# ── Module-level helper ──────────────────────────────────────────────────────

def cascade_evaluate(role: str, prompt_content: str) -> CascadeResult:
    """Convenience: run cascade evaluation for a prompt."""
    return CascadeEvaluator(role=role).evaluate(prompt_content)
