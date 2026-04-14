"""
evaluate.py — ShinkaEvolve evaluation adapter for AndrusAI.

Bridges ShinkaEvolve's population/fitness model to AndrusAI's existing
ExperimentRunner and composite_score infrastructure.

ShinkaEvolve calls this module's evaluate() function for each candidate
in the population. The function returns a metrics dict that ShinkaEvolve
uses for MAP-Elites selection and fitness ranking.

Usage:
    shinka-evolve run --evaluate workspace/shinka/evaluate.py
"""

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Add app to path when running from ShinkaEvolve
if str(Path("/app")) not in sys.path:
    sys.path.insert(0, str(Path("/app")))


def evaluate(program_path: str, results_dir: str) -> dict:
    """ShinkaEvolve evaluation function using AndrusAI's existing metrics.

    Args:
        program_path: Path to the evolved program/file to evaluate.
        results_dir: Directory where evaluation results should be written.

    Returns:
        Dict with metrics: composite_score, task_pass_rate, safety_ok, etc.
    """
    from app.metrics import compute_metrics, composite_score
    from app.experiment_runner import load_test_tasks, validate_response
    from app.llm_factory import create_specialist_llm

    results = {
        "composite_score": 0.0,
        "task_pass_rate": 0.0,
        "safety_ok": True,
        "details": [],
    }

    try:
        # Read the evolved content
        evolved_content = Path(program_path).read_text()

        # Run a subset of test tasks
        tasks = load_test_tasks("fixed")[:10]
        if not tasks:
            results["details"].append("No test tasks available")
            return results

        # Generate responses using the evolved prompt/code
        llm = create_specialist_llm(max_tokens=1024, role="coding")
        passed = 0
        total = 0

        for task_info in tasks:
            task_text = task_info.get("task", "")
            validation_rule = task_info.get("validation", "")

            try:
                # Use evolved content as context for the response
                prompt = f"{evolved_content[:2000]}\n\nTask: {task_text}"
                response = str(llm.call(prompt)).strip()

                if validate_response(response, validation_rule):
                    passed += 1
                total += 1
            except Exception as e:
                logger.warning(f"Task evaluation failed: {e}")
                total += 1

        if total > 0:
            results["task_pass_rate"] = passed / total

        # Get current composite score
        results["composite_score"] = composite_score()

        # Write results to file for ShinkaEvolve
        results_path = Path(results_dir) / "eval_results.json"
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps(results, indent=2))

    except Exception as e:
        logger.error(f"ShinkaEvolve evaluation failed: {e}")
        results["details"].append(f"Error: {str(e)[:200]}")

    return results


if __name__ == "__main__":
    # CLI interface for ShinkaEvolve
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", required=True)
    parser.add_argument("--results-dir", required=True)
    args = parser.parse_args()

    result = evaluate(args.program, args.results_dir)
    print(json.dumps(result, indent=2))
