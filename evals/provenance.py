#!/usr/bin/env python3
"""Attach control-plane provenance to an eval report.

Phase 1 of docs/EVAL_HARNESS_V2_PLAN.md. The harness POSTs a prompt and reads a
string, so its numbers cannot be interpreted: on 2026-07-25 the same report
questions went to ``deep_research`` (gated) at 14:32-16:30 and to plain
``research`` (ungated) at 17:51, and "12/12" averaged over materially different
systems. The gateway already records which crew ran, what it cost and what it
returned — this joins to it.

Run it where the control-plane credentials live (no duplication):

    docker compose run --rm --no-deps -v "$PWD/evals:/app/evals" gateway \
        python evals/provenance.py evals/results/<report>.json \
        --window 2026-07-25T17:00 2026-07-25T18:10

Joining strategy
----------------
* Reports written after this change carry per-question ``started_at`` /
  ``ended_at`` (UTC ISO), so the join is exact.
* Older reports have only ``latency_s``. For those, pass ``--window`` and the
  prompt is matched against ``control_plane.tickets.description`` (which holds
  the untruncated prompt; ``title`` is truncated). Best-effort, and labelled as
  such in the output.

Honesty constraints wired in deliberately
-----------------------------------------
* ``gate`` is derived from result TEXT, because no column records the evidence
  gate's verdict. When it cannot be determined the field says so rather than
  guessing.
* ``cost`` is reported per crew row and explicitly flagged unreliable: nested
  crews record the shared request tracker's totals at stop, so a deep_research
  row can echo its parent's figure. See reports/GATE_DIAGNOSIS_2026-07-25.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Commander's crew row opens before the ticket is created (observed ~23s), so a
# window built from the ticket needs a lookback to catch routing.
_TICKET_LOOKBACK_S = 120
_TICKET_MAX_RUN_S = 2700  # main.py's hard ceiling

_GATE_BLOCK_HINTS = (
    ("evidence gate did not clear", "blocked"),
    ("research-evidence gate escalated", "blocked"),
    ("anti-fabrication verification", "blocked"),
    ("retrieved no evidence sources", "blocked_no_evidence"),
)

COST_CAVEAT = (
    "per-crew cost/token figures are UNRELIABLE: nested crews record the shared "
    "request tracker's totals at stop, so a child row can echo its parent's "
    "figure (see reports/GATE_DIAGNOSIS_2026-07-25.md)"
)


def _db():
    # Running this as a script puts sys.path[0] at evals/, so the repo root —
    # and therefore ``app`` — is not importable without help.
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from app.control_plane.db import execute
    return execute


def _parse_iso(value: str) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _find_ticket(execute, prompt: str, window: tuple | None) -> dict | None:
    """Locate the ticket for this prompt. ``description`` holds the full prompt."""
    sql = (
        "SELECT id, left(title, 120) AS title, description, status, difficulty, "
        "       cost_usd, tokens_used, created_at, completed_at, "
        "       length(result_summary) AS result_len "
        "  FROM control_plane.tickets "
        " WHERE description = %s"
    )
    params: list = [prompt]
    if window:
        sql += " AND created_at >= %s AND created_at <= %s"
        params += [window[0], window[1]]
    sql += " ORDER BY created_at DESC LIMIT 1"
    rows = execute(sql, tuple(params), fetch=True) or []
    return rows[0] if rows else None


def _crew_rows(execute, start: datetime, end: datetime) -> list[dict]:
    rows = execute(
        "SELECT crew, state, left(coalesce(result_preview,''), 400) AS preview, "
        "       coalesce(error,'') AS error, tokens_used, cost_usd, "
        "       started_at, completed_at, is_sub_agent "
        "  FROM control_plane.crew_tasks "
        " WHERE started_at >= %s AND started_at <= %s "
        " ORDER BY started_at",
        (start, end), fetch=True,
    ) or []
    return rows


def _derive_gate(crew_rows: list[dict], reply: str) -> dict:
    """Infer the evidence gate's verdict from result text.

    No column records it, so this is text-derived and says so. A ``deep_research``
    crew that ran at all means the gate chain executed; plain ``research`` has no
    gate, which is itself the finding worth surfacing.
    """
    crews = {r["crew"] for r in crew_rows}
    if "deep_research" not in crews:
        return {
            "ran": False,
            "verdict": None,
            "why": "no deep_research crew in window — the plain research crew has no evidence gate",
            "source": "crew_rows",
        }
    haystacks = [reply or ""] + [r.get("preview") or "" for r in crew_rows]
    for text in haystacks:
        lowered = text.lower()
        for hint, verdict in _GATE_BLOCK_HINTS:
            if hint in lowered:
                return {"ran": True, "verdict": verdict, "why": hint,
                        "source": "result_text"}
    return {
        "ran": True,
        "verdict": "presumed_clear",
        "why": "deep_research completed and no block phrasing found; NOT a "
               "positive confirmation — no column records the gate verdict",
        "source": "result_text",
    }


def provenance_for(
    execute, result: dict, window: tuple | None, floor: datetime | None = None,
) -> dict:
    """Build the provenance block for one eval result.

    ``floor`` clamps a best-effort window so it cannot reach back into the
    previous question's crews.
    """
    exact = bool(result.get("started_at") and result.get("ended_at"))
    if exact:
        start = _parse_iso(result["started_at"]) - timedelta(seconds=5)
        end = _parse_iso(result["ended_at"]) + timedelta(seconds=30)
        ticket = _find_ticket(execute, result["prompt"], (start, end))
    else:
        ticket = _find_ticket(execute, result["prompt"], window)
        if not ticket:
            return {
                "join": "failed",
                "why": "no ticket matched this prompt in the given window; pass "
                       "--window, or re-run with a harness that records "
                       "started_at/ended_at",
            }
        created = ticket["created_at"]
        start = created - timedelta(seconds=_TICKET_LOOKBACK_S)
        if floor is not None and floor > start:
            start = floor
        end = ticket["completed_at"] or (created + timedelta(seconds=_TICKET_MAX_RUN_S))

    crew_rows = _crew_rows(execute, start, end)
    serving = [r for r in crew_rows if r["crew"] != "self_improvement"]
    background = sorted({r["crew"] for r in crew_rows if r["crew"] == "self_improvement"})

    return {
        "join": "exact" if exact else "best_effort_window_match",
        "ticket": None if not ticket else {
            "status": ticket["status"],
            "difficulty": ticket["difficulty"],
            "cost_usd": float(ticket["cost_usd"] or 0),
            "tokens_used": int(ticket["tokens_used"] or 0),
            "result_chars": ticket["result_len"],
            "created_at": ticket["created_at"].isoformat(),
        },
        "crews": [
            {
                "crew": r["crew"],
                "state": r["state"],
                "duration_s": round(
                    ((r["completed_at"] or r["started_at"]) - r["started_at"]).total_seconds(), 1
                ),
                "tokens_used": int(r["tokens_used"] or 0),
                "cost_usd": float(r["cost_usd"] or 0),
                "error": (r["error"] or "")[:200] or None,
            }
            for r in serving
        ],
        "crew_sequence": [r["crew"] for r in serving],
        "gate": _derive_gate(serving, result.get("reply") or result.get("reply_preview", "")),
        "background_jobs_in_window": background,
        "cost_caveat": COST_CAVEAT,
    }


def attach(report_path: Path, window: tuple | None) -> dict:
    payload = json.loads(report_path.read_text())
    execute = _db()

    # Best-effort joins bleed: a window built from one ticket with a lookback
    # catches the tail of the PREVIOUS question's crews (observed as a stray
    # leading `critic`). The harness runs questions sequentially, so clamping
    # each window's start to the previous question's ticket removes it. Exact
    # joins (reports carrying started_at/ended_at) don't need this.
    prev_ticket_at = None
    joined = 0
    for result in payload["results"]:
        prov = provenance_for(execute, result, window, floor=prev_ticket_at)
        result["provenance"] = prov
        if prov.get("join") != "failed":
            joined += 1
        ticket = prov.get("ticket") or {}
        if ticket.get("created_at"):
            prev_ticket_at = _parse_iso(ticket["created_at"])
    payload["provenance_attached"] = {
        "questions": len(payload["results"]),
        "joined": joined,
        "failed": len(payload["results"]) - joined,
    }
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("--window", nargs=2, metavar=("START", "END"),
                    help="UTC ISO bounds for matching older reports that lack "
                         "per-question timestamps.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Defaults to writing back in place.")
    args = ap.parse_args()

    window = None
    if args.window:
        window = (_parse_iso(args.window[0]), _parse_iso(args.window[1]))

    payload = attach(args.report, window)
    dest = args.out or args.report
    dest.write_text(json.dumps(payload, indent=2, default=str) + "\n")

    stats = payload["provenance_attached"]
    print(f"joined {stats['joined']}/{stats['questions']} "
          f"({stats['failed']} failed) -> {dest}", file=sys.stderr)
    print()
    print(f"{'question':<28}{'crews':<38}{'gate':<18}")
    print("-" * 88)
    for r in payload["results"]:
        p = r.get("provenance", {})
        crews = ",".join(p.get("crew_sequence") or []) or "-"
        gate = p.get("gate") or {}
        verdict = "n/a" if gate.get("ran") is False else str(gate.get("verdict"))
        print(f"{r['id']:<28}{crews[:37]:<38}{verdict:<18}")


if __name__ == "__main__":
    main()
