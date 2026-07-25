"""Probe: how sensitive is the deep-research gate to the router's difficulty?

`assess_deep_research` is a deterministic keyword scorer, so the deep-vs-plain
fork can be measured exactly, with no LLM calls and no spend, by sweeping the
one non-deterministic input the router supplies: `difficulty`.

Run: docker compose run --rm --no-deps -w /app -e PYTHONPATH=/app \
        -v "$PWD/evals:/app/evals" gateway python evals/probe_route_sensitivity.py
"""

from __future__ import annotations

import json
import pathlib

from app.research.deep_path import assess_deep_research

GOLDEN = pathlib.Path(__file__).with_name("golden_set.jsonl")


def main() -> None:
    rows = [
        json.loads(line)
        for line in GOLDEN.read_text().splitlines()
        if line.strip()
    ]

    print(f"{'question':28} {'score@d1..d10':>13}  {'thr':>3}  deep-at-difficulty")
    print("-" * 92)

    flippable: list[tuple[str, int]] = []
    for row in rows:
        qid = row.get("id", "?")
        prompt = row.get("prompt") or row.get("question") or ""
        per_difficulty = {
            d: assess_deep_research(prompt, difficulty=d) for d in range(1, 11)
        }

        deep_at = [d for d, a in per_difficulty.items() if a.use_deep]
        base, top = per_difficulty[1], per_difficulty[10]
        span = f"{base.score}..{top.score}"
        if len(deep_at) == 10:
            verdict = "always deep"
        elif not deep_at:
            verdict = "never deep"
        else:
            verdict = f"deep only at d>={min(deep_at)}  <-- FLIPPABLE"
            flippable.append((qid, min(deep_at)))
        print(f"{qid:28} {span:>13}  {base.threshold:>3}  {verdict}")

    print()
    print("structural score (difficulty-independent points only, d=1):")
    for row in rows:
        qid = row.get("id", "?")
        prompt = row.get("prompt") or row.get("question") or ""
        a = assess_deep_research(prompt, difficulty=1)
        print(f"  {qid:28} {a.score}/{a.threshold}  {', '.join(a.reasons) or '-'}")

    print()
    print(
        f"flippable on difficulty alone: {len(flippable)}/{len(rows)} -> "
        f"{[q for q, _ in flippable]}",
    )
    print()
    print("note: promotion only inspects decisions with crew == 'research';")
    print("      any other crew choice bypasses the deep gate entirely.")


if __name__ == "__main__":
    main()
