"""
ShinkaEvolve evaluator for AndrusAI agent evolution.

This evaluator is called by ShinkaEvolve for each candidate program.
It imports the evolved code and runs the fixed test suite to measure fitness.

The evaluator uses run_shinka_eval() which:
  1. Imports the candidate program
  2. Calls run_evaluation() from it
  3. Validates the output
  4. Saves metrics to results_dir
"""
import os
import argparse
from typing import Any, Dict, List, Optional, Tuple

from shinka.core import run_shinka_eval


def validate_evaluation(
    run_output: Tuple[float, dict],
) -> Tuple[bool, Optional[str]]:
    """Validate that the evolved program produces valid evaluation results.

    Args:
        run_output: (combined_score, details_dict) from run_evaluation()

    Returns:
        (is_valid, error_message)
    """
    if not isinstance(run_output, tuple) or len(run_output) != 2:
        return False, f"Expected (score, dict) tuple, got {type(run_output)}"

    score, details = run_output

    if not isinstance(score, (int, float)):
        return False, f"Score must be numeric, got {type(score)}"

    if not (0.0 <= score <= 1.0):
        return False, f"Score {score} outside [0.0, 1.0] range"

    if not isinstance(details, dict):
        return False, f"Details must be dict, got {type(details)}"

    return True, None


def aggregate_metrics(
    results: List[Tuple[float, dict]],
) -> Dict[str, Any]:
    """Aggregate evaluation results into ShinkaEvolve metrics format.

    Args:
        results: List of (combined_score, details_dict) from run_evaluation()

    Returns:
        Dict with 'combined_score' (fitness) and 'public'/'private' metrics.
    """
    if not results:
        return {"combined_score": 0.0, "error": "No results"}

    score, details = results[0]

    return {
        "combined_score": float(score),
        "public": {
            "tool_accuracy": details.get("tool_accuracy", 0.0),
            "route_accuracy": details.get("route_accuracy", 0.0),
        },
        "private": {
            "tool_correct": details.get("tool_correct", 0),
            "tool_total": details.get("tool_total", 0),
            "route_correct": details.get("route_correct", 0),
            "route_total": details.get("route_total", 0),
        },
    }


def main(program_path: str, results_dir: str):
    """ShinkaEvolve evaluation entry point.

    Args:
        program_path: Path to the evolved program (candidate initial.py)
        results_dir: Directory to save metrics.json and correct.json
    """
    print(f"Evaluating program: {program_path}")
    print(f"Results dir: {results_dir}")
    os.makedirs(results_dir, exist_ok=True)

    metrics, correct, error_msg = run_shinka_eval(
        program_path=program_path,
        results_dir=results_dir,
        experiment_fn_name="run_evaluation",
        num_runs=1,
        validate_fn=validate_evaluation,
        aggregate_metrics_fn=aggregate_metrics,
    )

    if correct:
        print(f"Evaluation passed. Score: {metrics.get('combined_score', 0):.4f}")
    else:
        print(f"Evaluation failed: {error_msg}")

    return metrics, correct, error_msg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AndrusAI ShinkaEvolve evaluator")
    parser.add_argument("--program_path", type=str, default="initial.py")
    parser.add_argument("--results_dir", type=str, default="results")
    args = parser.parse_args()
    main(args.program_path, args.results_dir)
