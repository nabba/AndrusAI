"""evolver_job — the ephemeral-container entrypoint for the verified mutation engine.

Runs as ``python3 -m app.self_improvement.evolver_job`` inside a throwaway
"evolver" container (see ``Dockerfile.evolver``). The container bakes the repo
(incl. ``.git``) so it can cut worktrees from HEAD and run real ``pytest`` in its
OWN filesystem — which is what makes ``coding_session.runner`` (in-process
subprocess) work with no host process and no bridge.

I/O contract (container-native — no shared volume, per ``VOLUMES: 0`` on the
docker-proxy):
  * **Input**: the job spec arrives as JSON in ``$AAI_EVOLVE_JOB``
    (``{"target_file","approach","base"?,"budget_usd"?}``).
  * **Output**: a single JSON object on stdout, wrapped in sentinels so the
    gateway can extract it from any surrounding log noise:
        ``<<<EVOLVER_RESULT>>>{...}<<<EVOLVER_END>>>``
    All logging goes to stderr.

The editor is **anchored search/replace**, which is the robust answer to BOTH
old failure modes: it can only replace exact, unique substrings (so it cannot
regenerate a whole-file framework scaffold), and its output is a tiny edit list
(so the 8 KB/8192-token truncation problem never arises).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any, Callable, Optional

logger = logging.getLogger("evolver_job")

_RESULT_BEGIN = "<<<EVOLVER_RESULT>>>"
_RESULT_END = "<<<EVOLVER_END>>>"


# ── Anchored edit (the editor that can't produce a scaffold) ─────────────────


def apply_anchored_edits(source: str, edits: list[dict]) -> str:
    """Apply ``[{"find","replace"}, ...]`` to ``source``.

    Each ``find`` must occur EXACTLY ONCE — a missing anchor or an ambiguous
    (>1 match) anchor raises, so a vague edit fails loudly instead of silently
    mangling the file. This is what makes the editor incapable of the old
    whole-file scaffold rewrite: it can only touch anchored regions it located.
    """
    out = source
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict) or "find" not in edit or "replace" not in edit:
            raise ValueError(f"edit #{i} missing find/replace: {edit!r}")
        find = str(edit["find"])
        replace = str(edit["replace"])
        if find == "":
            raise ValueError(f"edit #{i} has empty find anchor")
        count = out.count(find)
        if count == 0:
            raise ValueError(f"edit #{i} anchor not found: {find[:60]!r}")
        if count > 1:
            raise ValueError(f"edit #{i} anchor not unique ({count}×): {find[:60]!r}")
        out = out.replace(find, replace, 1)
    return out


def parse_edits(raw: str) -> list[dict]:
    """Parse the LLM's edit response into a list of ``{find, replace}`` dicts.

    Tolerates ```json fences and leading/trailing prose. Raises on anything
    that isn't a JSON array of edit objects.
    """
    text = raw.strip()
    if "```" in text:
        # Pull the content of the first fenced block.
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("[") or p.startswith("{"):
                text = p
                break
    # If still wrapped in prose, slice to the outermost array.
    if not text.startswith("["):
        start, end = text.find("["), text.rfind("]")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("edits must be a JSON array")
    return data


def build_edit_prompt(spec: Any, approach: str) -> str:
    """The implementer prompt: the WHOLE file + the grounded contract + the
    approach, asking for a minimal anchored edit (NOT a rewrite)."""
    from app.self_improvement.change_spec import render_for_prompt

    return (
        "You are making a focused, minimal edit to one file of a Python project.\n"
        "You are given the COMPLETE current file and a contract you must not break.\n\n"
        f"## What to change\n{approach}\n\n"
        f"{render_for_prompt(spec)}\n\n"
        "## Output format — anchored edits ONLY\n"
        "Return ONLY a JSON array of edits, each `{\"find\": <exact unique substring "
        "of the current file>, \"replace\": <new text>}`. Rules:\n"
        "- Each `find` MUST be copied verbatim from the file above and occur EXACTLY ONCE.\n"
        "- Make the SMALLEST change that implements the requested approach.\n"
        "- DO NOT rewrite the whole file. DO NOT remove any public API listed in the contract.\n"
        "- Include enough surrounding context in each `find` to make it unique.\n"
        "Example: [{\"find\": \"def run(self, x):\\n        return foo(x)\", "
        "\"replace\": \"def run(self, x):\\n        return bar(x)\"}]\n"
    )


def make_anchored_editor(
    llm_call: Callable[[str], str], *, max_edits: int = 12
) -> Callable[..., str]:
    """Build an ``editor_fn(spec, approach) -> new_source`` from an LLM call."""

    def editor(spec: Any, approach: str) -> str:
        raw = llm_call(build_edit_prompt(spec, approach))
        edits = parse_edits(raw)
        if not edits:
            raise ValueError("LLM returned no edits")
        if len(edits) > max_edits:
            raise ValueError(f"too many edits ({len(edits)} > {max_edits}) — refusing")
        return apply_anchored_edits(spec.full_source, edits)

    return editor


# ── Production wirings (lazy — only constructed inside the container) ─────────


def _default_llm_call() -> Callable[[str], str]:
    from app.llm_factory import create_specialist_llm

    llm = create_specialist_llm(max_tokens=4096, role="coding")
    return lambda prompt: str(llm.call(prompt))


def _default_judge_call() -> Optional[Callable[[str], str]]:
    try:
        from app.llm_factory import create_vetting_llm

        judge = create_vetting_llm()
        return lambda prompt: str(judge.call(prompt))
    except Exception as exc:  # pragma: no cover
        logger.warning("judge LLM unavailable: %s", exc)
        return None


def _default_entry_point_runner() -> Callable[[str, dict], str]:
    """Run a benchmark task's entry expression against the code in ``code_root``.

    The task provides ``entry_module`` and ``entry_expr`` (a Python expression
    evaluated with ``inp`` bound to the task ``input``). Best-effort; used only
    when an operator-curated quality benchmark targets the changed file.
    """

    def runner(code_root: str, task: dict) -> str:
        module = task.get("entry_module", "")
        expr = task.get("entry_expr", "")
        if not module or not expr:
            return ""
        snippet = (
            "import json,sys\n"
            f"from {module} import *\n"
            "inp=json.loads(sys.argv[1])\n"
            f"print({expr})\n"
        )
        try:
            proc = subprocess.run(
                ["python3", "-c", snippet, json.dumps(task.get("input", ""))],
                cwd=code_root,
                capture_output=True,
                text=True,
                timeout=int(task.get("timeout_s", 180)),
            )
            return (proc.stdout or "").strip() or (proc.stderr or "").strip()[:500]
        except Exception as exc:
            return f"[entry_point error: {exc}]"

    return runner


def run_job(
    job: dict,
    *,
    llm_call: Optional[Callable[[str], str]] = None,
    judge_call: Optional[Callable[[str], str]] = None,
    entry_point_runner: Optional[Callable[[str, dict], str]] = None,
) -> dict:
    """Execute one verified-mutation job and return the result dict."""
    from app.coding_session.iterate import IterateConfig
    from app.self_improvement.pipeline import run_pipeline

    target_file = job["target_file"]
    approach = job.get("approach", "")
    base = job.get("base", "HEAD")
    budget = float(job.get("budget_usd", 5.0))

    editor = make_anchored_editor(llm_call or _default_llm_call())
    judge = judge_call if judge_call is not None else _default_judge_call()
    runner = entry_point_runner or _default_entry_point_runner()

    # Split the per-cycle budget: most of it for the iterate fix loop.
    config = IterateConfig(max_iterations=8, budget_usd=max(0.1, budget * 0.6))

    result = run_pipeline(
        target_file,
        approach,
        editor_fn=editor,
        config=config,
        entry_point_runner=runner,
        judge_call=judge,
    )
    return result.to_dict()


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raw = os.environ.get("AAI_EVOLVE_JOB", "")
    if not raw and argv:
        raw = argv[0]
    try:
        job = json.loads(raw) if raw else {}
        if not job.get("target_file"):
            raise ValueError("job spec missing 'target_file'")
        out: dict[str, Any] = {"ok": True, "result": run_job(job)}
    except Exception as exc:
        logger.exception("evolver_job failed")
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # Single clean result line on stdout, sentinel-wrapped for robust extraction.
    sys.stdout.write(_RESULT_BEGIN + json.dumps(out) + _RESULT_END + "\n")
    sys.stdout.flush()
    return 0 if out.get("ok") else 1


def extract_result(logs: str) -> dict:
    """Gateway-side helper: pull the result JSON out of the container logs."""
    begin = logs.rfind(_RESULT_BEGIN)
    end = logs.rfind(_RESULT_END)
    if begin < 0 or end < 0 or end <= begin:
        raise ValueError("no evolver result sentinel found in logs")
    payload = logs[begin + len(_RESULT_BEGIN) : end]
    return json.loads(payload)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
