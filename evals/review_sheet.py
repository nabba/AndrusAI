#!/usr/bin/env python3
"""Render an eval report as a human review sheet.

Phase 1 of docs/EVAL_HARNESS_V2_PLAN.md. The harness stops claiming to score and
starts presenting evidence for a decision: each question's acceptance contract,
its provenance (which crew ran, whether a gate ran), and the FULL reply, with a
verdict line to fill in.

Human reading is currently the only reliable scorer — it is what caught all three
failures of the substring scorer. This makes that reading cheap and auditable,
and produces the labelled set that Phase 2's automated scorer must reproduce.

    python evals/review_sheet.py evals/results/<report>.json --out sheet.md

No DB or network access needed; run provenance.py first if you want the crew and
gate columns populated.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_GOLDEN = Path(__file__).parent / "golden_set.jsonl"

# Cheap syntactic hints for the reviewer. NOT a verdict — the reviewer decides.
# These are the shapes observed being scored as successes on 2026-07-25.
_LEAK_HINTS = (
    ("call:", "possible raw tool-call syntax"),
    ("Thought:", "possible ReAct scratchpad"),
    ("Action:", "possible ReAct scratchpad"),
    ("Traceback", "possible traceback"),
    ("OSError", "possible traceback"),
    ("build failed:", "possible crash message"),
    ("--- SubIA Context ---", "SubIA scaffolding leaked"),
    ("[researcher]", "multi-agent phase transcript"),
    ("general knowledge", "possible ungrounded-by-disclosure"),
    ("cannot be answered from the retrieved evidence", "possible honest non-answer"),
    ("I cannot complete this request", "possible refusal"),
)


def _load_contracts() -> dict:
    out = {}
    for line in _GOLDEN.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["id"]] = row
    return out


def _hints(reply: str) -> list[str]:
    found = []
    for needle, label in _LEAK_HINTS:
        if needle.lower() in (reply or "").lower() and label not in found:
            found.append(label)
    return found


def _fmt_contract(contract: dict) -> str:
    lines = [f"**Intent:** {contract['intent']}", ""]
    lines.append(f"- shape: `{contract['answer_shape']}`")
    subs = ", ".join(f"{k}≥{v}" for k, v in (contract.get("min_substance") or {}).items())
    if subs:
        lines.append(f"- substance: {subs}")
    cit = contract.get("citation") or {}
    if cit.get("min_distinct_sources"):
        resolve = " (must resolve to run evidence)" if cit.get("must_resolve_to_run_evidence") else ""
        lines.append(f"- citations: ≥{cit['min_distinct_sources']}{resolve}")
    else:
        lines.append("- citations: not required")
    lines.append("")
    lines.append("**Must address:**")
    for item in contract.get("must_address", []):
        lines.append(f"- [ ] {item}")
    lines.append("")
    lines.append("**Must not:**")
    for item in contract.get("must_not", [])[:8]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append(f"**Degradation:** {contract.get('degradation','—')}")
    lines.append("")
    lines.append(f"**Ambiguity (resolved):** {contract.get('ambiguity','—')}")
    return "\n".join(lines)


def _fmt_provenance(prov: dict | None) -> str:
    if not prov:
        return "_No provenance attached — run `evals/provenance.py` first._"
    if prov.get("join") == "failed":
        return f"_Provenance join FAILED: {prov.get('why')}_"
    crews = " → ".join(prov.get("crew_sequence") or []) or "—"
    gate = prov.get("gate") or {}
    if gate.get("ran") is False:
        gate_txt = f"**no gate ran** — {gate.get('why','')}"
    else:
        gate_txt = f"{gate.get('verdict')} ({gate.get('why','')}; source: {gate.get('source')})"
    rows = [
        f"- join: `{prov.get('join')}`",
        f"- crews: **{crews}**",
        f"- gate: {gate_txt}",
    ]
    ticket = prov.get("ticket")
    if ticket:
        rows.append(
            f"- ticket: status={ticket['status']} difficulty={ticket['difficulty']} "
            f"result_chars={ticket['result_chars']}"
        )
    for crew in prov.get("crews") or []:
        err = f" ERROR={crew['error']}" if crew.get("error") else ""
        rows.append(
            f"  - `{crew['crew']}` {crew['state']} {crew['duration_s']}s "
            f"tokens={crew['tokens_used']} cost=${crew['cost_usd']:.4f}{err}"
        )
    if prov.get("background_jobs_in_window"):
        rows.append(f"- background jobs in window: {prov['background_jobs_in_window']}")
    rows.append(f"- ⚠️ {prov.get('cost_caveat','')}")
    return "\n".join(rows)


def render(payload: dict, contracts: dict) -> str:
    summary = payload.get("summary", {})
    out: list[str] = []
    out.append("# Eval review sheet")
    out.append("")
    out.append(f"- report base_url: `{payload.get('base_url')}`  sender: `{payload.get('sender')}`")
    out.append(f"- run valid: **{summary.get('valid')}**  credit errors: {summary.get('credit_errors', '?')}")
    out.append(
        f"- harness `delivery_rate`: **{summary.get('delivery_rate')}** "
        f"— TRANSPORT ONLY, not a quality figure"
    )
    out.append("")
    out.append("Score each question against its contract. Three outcomes:")
    out.append("")
    out.append("| outcome | meaning |")
    out.append("|---|---|")
    out.append("| `pass` | satisfies the contract |")
    out.append("| `fail` | does not — **including** content presented as an answer that isn't grounded when grounding is required |")
    out.append("| `blocked_infrastructure` | honestly names an external cause AND withholds invented content; excluded from the quality denominator |")
    out.append("")
    out.append("> An ungrounded answer that **discloses** being ungrounded is `fail`, "
               "not `blocked_infrastructure`. Disclosure is not a substitute for evidence.")
    out.append("")
    out.append("---")

    for result in payload.get("results", []):
        qid = result["id"]
        entry = contracts.get(qid, {})
        contract = entry.get("contract")
        reply = result.get("reply") or result.get("reply_preview") or ""
        truncated = not result.get("reply") and result.get("reply_chars", 0) > len(reply)

        out.append("")
        out.append(f"## {qid}")
        out.append("")
        out.append(f"> {result['prompt']}")
        out.append("")
        out.append(
            f"`{result['reply_chars']} chars` · `{result['latency_s']}s` · "
            f"harness={'delivered' if result.get('delivered') else 'FAILED'}"
            + (f" · marker=`{result['failure_marker']}`" if result.get("failure_marker") else "")
            + (f" · credit_errors={result['credit_errors']}" if result.get("credit_errors") else "")
        )
        out.append("")
        out.append("### Contract")
        out.append("")
        out.append(_fmt_contract(contract) if contract else "_No contract — Phase 0 incomplete for this question._")
        out.append("")
        out.append("### Provenance")
        out.append("")
        out.append(_fmt_provenance(result.get("provenance")))
        out.append("")
        hints = _hints(reply)
        if hints:
            out.append(f"### ⚠️ Syntactic hints (not a verdict): {'; '.join(hints)}")
            out.append("")
        out.append("### Reply")
        out.append("")
        if truncated:
            out.append(
                f"_⚠️ Only a {len(reply)}-char preview is stored — this report "
                f"predates full-reply capture, so groundedness cannot be judged._"
            )
            out.append("")
        out.append("```text")
        out.append(reply.strip() or "(empty)")
        out.append("```")
        out.append("")
        out.append("### Verdict")
        out.append("")
        out.append("- outcome: `pass` / `fail` / `blocked_infrastructure` → **______**")
        out.append("- clause it turns on: **______**")
        out.append("")
        out.append("---")

    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    payload = json.loads(args.report.read_text())
    sheet = render(payload, _load_contracts())
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(sheet)
        print(f"wrote {args.out} ({len(sheet):,} chars)")
    else:
        print(sheet)


if __name__ == "__main__":
    main()
