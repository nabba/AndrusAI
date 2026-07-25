#!/usr/bin/env python3
"""Answering-pipeline golden-set eval runner.

Part of the Phase 1 groundwork in docs/ANSWERING_V2_PLAN.md. Sends each
question in golden_set.jsonl through the SAME dispatch path Signal uses
(``POST /api/cp/chat/send`` -> ``Commander().handle()``) and scores the
result on delivery + basic completeness heuristics, so future answering-
pipeline changes can be compared against a recorded baseline instead of
asserted.

*** THIS SPENDS REAL LLM BUDGET AND WRITES REAL DATA ***
Every question is a genuine dispatch through the live Commander — it
incurs real per-role LLM cost, creates real control_plane.crew_tasks /
audit.log / ticket rows, and (if the conversation-history join means a
"sender" is shared with the real operator) becomes part of that
conversation's history. Never run this against a shared production
gateway without the operator's awareness of the cost and the resulting
synthetic conversation entries. Use --sender to isolate the eval run's
conversation history from the real owner's thread.

Usage:
    python evals/run_eval.py --base-url http://127.0.0.1:8765 \\
        --sender eval-harness --out evals/results/baseline.json

    # Re-run later and diff against a recorded baseline:
    python evals/run_eval.py --out evals/results/after_phase2.json
    python evals/run_eval.py --diff evals/results/baseline.json \\
        evals/results/after_phase2.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

_GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.jsonl"
_ERROR_LOG_PATH = Path(__file__).resolve().parents[1] / "workspace/logs/errors.jsonl"

# ── Credit-outage guard (2026-07-25) ────────────────────────────────────
#
# The first live baseline recorded 2/12 delivered and was read as a quality
# measurement. It was not. The run coincided with an OpenRouter credit outage:
# 69 HTTP 402s in 38 minutes, every one of them failing over to
# ``ollama/llama3.1:8b`` — a model that cannot call tools. The first 402 landed
# during question 2; question 1, which ran before it, produced a complete
# report. So five of the six questions that completed were answered by an 8B
# local fallback, and the "baseline" measured a credit-exhausted system.
#
# Nothing in the harness could see that, which is the actual defect: an
# instrument that cannot detect its own invalid conditions will keep producing
# confident wrong numbers. So the harness now refuses to start during an
# outage, counts credit errors per question, and aborts the moment one appears
# mid-run rather than spending budget on a run that is already void.
#
# Full account: reports/GATE_DIAGNOSIS_2026-07-25.md
_CREDIT_ERROR_MARKERS = (
    "status=402",
    "failover: credit error",
    "insufficient credits",
    "insufficient_credits",
)

# How far back the pre-flight looks for credit errors.
_PREFLIGHT_WINDOW_S = 1800.0

# Tail bytes scanned by the pre-flight. errors.jsonl runs to tens of MB; the
# recent window is always at the end.
_PREFLIGHT_TAIL_BYTES = 4 * 1024 * 1024


def _line_has_credit_error(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in _CREDIT_ERROR_MARKERS)


class CreditErrorWatcher:
    """Counts provider credit (402) errors in the gateway's structured log.

    Two jobs: a look-back for the pre-flight, and an incremental tail poll so
    each question can be attributed the credit errors that occurred during it.

    Degrades explicitly, never silently: when the log is unreadable
    ``available`` is False and the caller must decide out loud, because a quiet
    "no errors found" is indistinguishable from "not looking" and that is how
    the invalid baseline happened.
    """

    def __init__(self, log_path: Path = _ERROR_LOG_PATH) -> None:
        self.log_path = log_path
        self.available = False
        self.unavailable_reason = ""
        self._offset = 0
        try:
            self._offset = log_path.stat().st_size
            self.available = True
        except Exception as exc:
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"

    def recent(self, window_s: float = _PREFLIGHT_WINDOW_S) -> int:
        """Credit errors logged within the last ``window_s`` seconds."""
        if not self.available:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_s)
        count = 0
        try:
            with open(self.log_path, "rb") as handle:
                size = handle.seek(0, 2)
                start = max(0, size - _PREFLIGHT_TAIL_BYTES)
                handle.seek(start)
                if start:
                    # Only when we seeked INTO the file is the first line a
                    # partial one. Discarding it unconditionally would drop the
                    # only entry in a short log.
                    handle.readline()
                for raw in handle:
                    line = raw.decode("utf-8", errors="replace")
                    if not _line_has_credit_error(line):
                        continue
                    try:
                        stamp = datetime.fromisoformat(
                            json.loads(line).get("ts", "")
                        )
                    except Exception:
                        continue
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    if stamp >= cutoff:
                        count += 1
        except Exception as exc:
            self.available = False
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
        return count

    def poll(self) -> int:
        """Credit errors appended since the previous poll."""
        if not self.available:
            return 0
        count = 0
        try:
            with open(self.log_path, "rb") as handle:
                size = handle.seek(0, 2)
                if size < self._offset:  # rotated
                    self._offset = 0
                handle.seek(self._offset)
                for raw in handle:
                    if _line_has_credit_error(
                        raw.decode("utf-8", errors="replace")
                    ):
                        count += 1
                self._offset = handle.tell()
        except Exception as exc:
            self.available = False
            self.unavailable_reason = f"{type(exc).__name__}: {exc}"
        return count


class CreditOutage(RuntimeError):
    """Raised when a run must not start or must not continue."""

# Structural failure markers pulled directly from the code paths that
# produce them (app/agents/commander/orchestrator.py's _merge_crew_results
# apology strings, the main.py soft/hard-timeout apologies). A hit here
# means the pipeline gave up rather than answered — independent of
# whether the ANSWER itself is any good.
_GAVE_UP_MARKERS = (
    "wasn't able to put together an answer",
    "didn't finish in time or",
    "trouble understanding that request",
    "ran for 10+ minutes without",
    "ran for 20+ minutes without",
    "hit the absolute 45-minute ceiling",
    "stalled (no LLM activity",
    "currently handling",  # load-shed message
)

# 2026-07-24: the FIRST baseline run exposed a flaw in this instrument.
# Six of twelve replies scored "delivered" on the original markers alone
# while actually being explicit refusals or artifact-delivery failures —
# the pipeline RAN (sometimes for 19 minutes), then declined to hand over
# a result. Those are answer-quality failures from the user's point of
# view and must not be counted as delivered, but they're a genuinely
# different diagnostic class from "gave up / timed out" above, so they're
# tracked separately rather than merged into one bucket.
#
# Sources: the epistemic/critic gate refusal path, the deep_research
# artifact hand-off, and the creative-crew empty-output path. Matched on
# apostrophe-free substrings so smart-vs-straight quotes ("I’m"/"I'm")
# can't cause a miss.
_REFUSAL_MARKERS = (
    "withholding the draft",
    "evidence gate did not clear",
    "could not be delivered as a PDF",
    "failed to produce any content",
    "contains no actual research",
    "won't present unsubstantiated claims",
    "will not present unsubstantiated claims",
)

_FAILURE_MARKERS = _GAVE_UP_MARKERS + _REFUSAL_MARKERS


@dataclass
class EvalResult:
    id: str
    category: str
    prompt: str
    ok: bool                 # HTTP-level success
    delivered: bool          # no structural failure marker present
    latency_s: float
    reply_chars: int
    failure_marker: str | None = None
    error: str | None = None
    reply_preview: str = ""
    # Provider credit (402) errors logged while this question was in flight.
    # Non-zero means the answer was probably served by the tool-incapable
    # local failover, so the row measures an outage rather than the pipeline.
    credit_errors: int = 0


@dataclass
class EvalReport:
    base_url: str
    sender: str
    results: list[EvalResult] = field(default_factory=list)
    # Run-level validity. A report that carries valid=False must never be
    # quoted as a baseline.
    valid: bool = True
    invalid_reason: str = ""
    credit_watch: str = "ok"

    def summary(self) -> dict:
        n = len(self.results)
        delivered = sum(1 for r in self.results if r.delivered)
        errored = sum(1 for r in self.results if not r.ok)
        refused = sum(
            1 for r in self.results
            if r.failure_marker in _REFUSAL_MARKERS
        )
        gave_up = sum(
            1 for r in self.results
            if r.failure_marker in _GAVE_UP_MARKERS
        )
        avg_latency = (
            sum(r.latency_s for r in self.results) / n if n else 0.0
        )
        credit_errors = sum(r.credit_errors for r in self.results)
        contaminated = sum(1 for r in self.results if r.credit_errors)
        return {
            "n": n,
            "delivered": delivered,
            "delivery_rate": round(delivered / n, 3) if n else 0.0,
            # Distinct diagnostic classes — all count against delivery:
            "refused_or_no_artifact": refused,  # ran, then withheld/failed hand-off
            "gave_up_or_timed_out": gave_up,    # pipeline abandoned the request
            "http_errors": errored,            # transport died (e.g. gateway restart)
            "avg_latency_s": round(avg_latency, 1),
            "max_latency_s": round(max((r.latency_s for r in self.results), default=0.0), 1),
            # Validity, not quality. Any credit error means at least one answer
            # came from the tool-incapable local failover.
            "valid": self.valid and credit_errors == 0,
            "invalid_reason": self.invalid_reason,
            "credit_errors": credit_errors,
            "questions_with_credit_errors": contaminated,
            "credit_watch": self.credit_watch,
        }


def load_golden_set(path: Path = _GOLDEN_SET_PATH) -> list[dict]:
    items = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _detect_failure_marker(reply: str) -> str | None:
    lower = reply.lower()
    for marker in _FAILURE_MARKERS:
        if marker.lower() in lower:
            return marker
    return None


def send_one(base_url: str, sender: str, prompt: str, timeout_s: float) -> tuple[str, float, str | None]:
    """POST to /api/cp/chat/send. Returns (reply, latency_s, error)."""
    url = f"{base_url.rstrip('/')}/api/cp/chat/send"
    payload = json.dumps({"message": prompt, "sender": sender}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read())
        return str(body.get("reply", "")), time.monotonic() - t0, None
    except urllib.error.HTTPError as exc:
        return "", time.monotonic() - t0, f"HTTP {exc.code}: {exc.read()[:300]}"
    except Exception as exc:
        return "", time.monotonic() - t0, f"{type(exc).__name__}: {exc}"


def preflight_credit_check(
    watcher: CreditErrorWatcher, *, allow: bool = False,
) -> str:
    """Refuse to start a run during a provider credit outage.

    Returns a short status string for the report. Raises :class:`CreditOutage`
    when the run must not start and ``allow`` is False.
    """
    if not watcher.available:
        status = f"unavailable ({watcher.unavailable_reason})"
        print(
            "!! cannot read the gateway error log at "
            f"{watcher.log_path} — {watcher.unavailable_reason}\n"
            "!! credit-outage detection is DISABLED for this run. If the "
            "provider runs out of credits mid-run, every remaining answer "
            "will come from a tool-incapable local model and the numbers "
            "will be meaningless. Pass --error-log to point at the real log.",
            file=sys.stderr,
        )
        return status
    recent = watcher.recent()
    if recent:
        message = (
            f"{recent} provider credit (402) errors in the last "
            f"{_PREFLIGHT_WINDOW_S / 60:.0f} minutes"
        )
        if not allow:
            raise CreditOutage(
                f"{message} — refusing to start.\n"
                "Answers during a credit outage are served by the local "
                "failover model, which cannot call tools; the resulting "
                "scores measure the outage, not the pipeline. This is exactly "
                "how the 2026-07-24 '2/12' baseline came to be believed.\n"
                "Top up the provider, wait for the window to clear, then "
                "re-run. Use --allow-credit-errors only to reproduce an "
                "outage deliberately."
            )
        print(f"!! proceeding despite {message} (--allow-credit-errors)", file=sys.stderr)
        return f"outage-at-start ({recent})"
    return "ok"


def run(
    base_url: str,
    sender: str,
    timeout_s: float,
    only_ids: set[str] | None,
    *,
    watcher: CreditErrorWatcher | None = None,
    allow_credit_errors: bool = False,
) -> EvalReport:
    report = EvalReport(base_url=base_url, sender=sender)
    items = load_golden_set()
    if only_ids:
        items = [i for i in items if i["id"] in only_ids]

    if watcher is None:
        watcher = CreditErrorWatcher()
    report.credit_watch = preflight_credit_check(
        watcher, allow=allow_credit_errors,
    )
    watcher.poll()  # discard anything already on disk; count only from here

    for item in items:
        print(f"-> {item['id']} ({item['category']}): {item['prompt'][:70]}...", file=sys.stderr)
        reply, latency, error = send_one(base_url, sender, item["prompt"], timeout_s)
        credit_errors = watcher.poll()
        marker = _detect_failure_marker(reply) if reply else None
        result = EvalResult(
            id=item["id"], category=item["category"], prompt=item["prompt"],
            ok=error is None, delivered=(error is None and marker is None and len(reply) >= 20),
            latency_s=round(latency, 1), reply_chars=len(reply),
            failure_marker=marker, error=error, reply_preview=reply[:200],
            credit_errors=credit_errors,
        )
        report.results.append(result)
        status = "OK" if result.delivered else ("FAIL:" + (marker or error or "?"))
        credit_note = f"  [{credit_errors} credit errors]" if credit_errors else ""
        print(
            f"   {status}  {latency:.0f}s  {len(reply)} chars{credit_note}",
            file=sys.stderr,
        )

        # Stop as soon as the run is void. Continuing would spend real budget
        # producing rows that measure the outage — and would risk the result
        # being quoted later as a baseline.
        if credit_errors and not allow_credit_errors:
            report.valid = False
            report.invalid_reason = (
                f"provider credit (402) errors appeared during '{item['id']}' "
                f"({credit_errors}); answers from here on would come from the "
                "tool-incapable local failover. Run aborted after "
                f"{len(report.results)}/{len(items)} questions."
            )
            print(f"\n!! ABORTING: {report.invalid_reason}", file=sys.stderr)
            break
    return report


def _print_diff(a: dict, b: dict) -> None:
    a_by_id = {r["id"]: r for r in a["results"]}
    b_by_id = {r["id"]: r for r in b["results"]}
    print(f"{'id':<28} {'before':<10} {'after':<10} {'lat before':<12} {'lat after':<10}")
    for qid in sorted(set(a_by_id) | set(b_by_id)):
        ra, rb = a_by_id.get(qid), b_by_id.get(qid)
        before = "delivered" if ra and ra["delivered"] else "FAILED" if ra else "missing"
        after = "delivered" if rb and rb["delivered"] else "FAILED" if rb else "missing"
        lb = f"{ra['latency_s']:.0f}s" if ra else "-"
        la = f"{rb['latency_s']:.0f}s" if rb else "-"
        print(f"{qid:<28} {before:<10} {after:<10} {lb:<12} {la:<10}")
    print()
    print("before:", a.get("summary"))
    print("after: ", b.get("summary"))


def rescore(path: Path) -> dict:
    """Re-score an existing report against the CURRENT marker lists, offline.

    Added 2026-07-24 after the first baseline run showed the original
    marker list scored explicit refusals as successes. Re-scoring in place
    costs nothing and spends no budget, so a corrected baseline doesn't
    require re-running 12 live questions against the gateway.

    Caveat: this can only see ``reply_preview`` (200 chars), so a marker
    appearing deeper in a long reply is invisible here — a re-scored
    report is a floor on the failure count, not a ceiling. Every refusal
    observed so far leads with its marker, so in practice it matches.
    """
    payload = json.loads(path.read_text())
    changed = []
    for r in payload["results"]:
        if not r.get("ok"):
            continue  # transport failure — already not delivered
        marker = _detect_failure_marker(r.get("reply_preview", "") or "")
        was = r.get("delivered")
        now = marker is None and r.get("reply_chars", 0) >= 20
        if marker and was != now:
            r["failure_marker"] = marker
            r["delivered"] = now
            changed.append((r["id"], marker))
    # Recompute the summary from the corrected rows.
    prior = payload.get("summary") or {}
    rep = EvalReport(
        base_url=payload.get("base_url", ""),
        sender=payload.get("sender", ""),
        # Validity is a property of how the run was CONDUCTED, so re-scoring
        # must carry it forward rather than silently re-blessing an invalid run.
        valid=bool(prior.get("valid", True)),
        invalid_reason=str(prior.get("invalid_reason", "")),
        credit_watch=str(prior.get("credit_watch", "unknown (pre-dates the credit guard)")),
    )
    rep.results = [
        EvalResult(
            id=r["id"], category=r["category"], prompt=r["prompt"], ok=r["ok"],
            delivered=r["delivered"], latency_s=r["latency_s"],
            reply_chars=r["reply_chars"], failure_marker=r.get("failure_marker"),
            error=r.get("error"), reply_preview=r.get("reply_preview", ""),
            credit_errors=int(r.get("credit_errors", 0) or 0),
        )
        for r in payload["results"]
    ]
    payload["summary"] = rep.summary()
    payload["rescored"] = True
    for qid, marker in changed:
        print(f"  reclassified {qid}: delivered -> FAILED ({marker})", file=sys.stderr)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8765")
    ap.add_argument("--sender", default="eval-harness",
                     help="Isolate the eval conversation from the real operator's Signal thread.")
    ap.add_argument("--timeout", type=float, default=900.0,
                     help="Per-question client timeout in seconds (report-class dispatch can take minutes).")
    ap.add_argument("--only", nargs="*", default=None, help="Run only these golden-set ids.")
    ap.add_argument("--out", type=Path, default=None, help="Write JSON report here.")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE_JSON", "AFTER_JSON"),
                     help="Skip running; just diff two previously-recorded reports.")
    ap.add_argument("--rescore", type=Path, default=None, metavar="REPORT_JSON",
                     help="Skip running; re-score an existing report against the "
                          "current marker lists (offline, spends no budget). "
                          "Writes in place unless --out is given.")
    ap.add_argument("--error-log", type=Path, default=_ERROR_LOG_PATH,
                     help="Gateway structured error log, used to detect provider "
                          "credit (402) outages that would invalidate the run.")
    ap.add_argument("--allow-credit-errors", action="store_true",
                     help="Run even during a provider credit outage. Off by "
                          "default: answers served by the local failover model "
                          "cannot call tools, so the scores measure the outage "
                          "rather than the pipeline. The resulting report is "
                          "marked valid=false.")
    args = ap.parse_args()

    if args.rescore:
        payload = rescore(args.rescore)
        dest = args.out or args.rescore
        dest.write_text(json.dumps(payload, indent=2))
        print(json.dumps(payload["summary"], indent=2))
        print(f"Re-scored report written to {dest}", file=sys.stderr)
        return

    if args.diff:
        before = json.loads(Path(args.diff[0]).read_text())
        after = json.loads(Path(args.diff[1]).read_text())
        _print_diff(before, after)
        return

    only_ids = set(args.only) if args.only else None
    try:
        report = run(
            args.base_url, args.sender, args.timeout, only_ids,
            watcher=CreditErrorWatcher(args.error_log),
            allow_credit_errors=args.allow_credit_errors,
        )
    except CreditOutage as exc:
        print(f"\nREFUSING TO RUN: {exc}", file=sys.stderr)
        raise SystemExit(2)
    payload = {
        "base_url": report.base_url,
        "sender": report.sender,
        "results": [asdict(r) for r in report.results],
        "summary": report.summary(),
    }
    print(json.dumps(payload["summary"], indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2))
        print(f"Report written to {args.out}", file=sys.stderr)
    if not payload["summary"]["valid"]:
        print(
            "\n!! THIS REPORT IS NOT A VALID BASELINE — "
            f"{report.invalid_reason or 'credit errors occurred during the run'}",
            file=sys.stderr,
        )
        raise SystemExit(3)


if __name__ == "__main__":
    main()
