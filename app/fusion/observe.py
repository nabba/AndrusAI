"""Fusion deliberation capture — observability for the /cp/fusion page.

When a raw completion is fused, OpenRouter's judge returns structured analysis
(consensus / contradictions / unique insights / blind spots) alongside the
final answer. We persist whatever the response carries to a capped JSONL so the
operator can see what the panel actually deliberated. Defensive extraction: the
exact response location is provider-versioned (the live ``selftest`` confirms
the shape), so we probe several likely spots and store what's present.

Persisted on the workspace bind mount; failure-isolated end-to-end so a capture
error never breaks a completion.
"""

from __future__ import annotations

import datetime
import json
import threading

from app.paths import WORKSPACE_ROOT

_LOCK = threading.Lock()
_FILE = WORKSPACE_ROOT / "fusion" / "deliberations.jsonl"
_CAP = 1000


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _extract(resp) -> dict:
    """Pull the final answer + any fusion/judge metadata, defensively."""
    out: dict = {}
    try:
        d = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    except Exception:
        d = {}
    if isinstance(d, dict):
        out["model"] = d.get("model")
        for k in ("router", "fusion", "deliberation", "annotations"):
            if d.get(k) is not None:
                out[k] = d[k]
        if d.get("usage") is not None:
            out["usage"] = d["usage"]
    try:
        out["answer_preview"] = (resp.choices[0].message.content or "")[:600]
    except Exception:
        pass
    try:
        msg = resp.choices[0].message
        for attr in ("annotations", "tool_calls", "reasoning"):
            v = getattr(msg, attr, None)
            if v:
                out[f"message_{attr}"] = v
    except Exception:
        pass
    hp = getattr(resp, "_hidden_params", None)
    if isinstance(hp, dict):
        if hp.get("response_cost") is not None:
            out["response_cost"] = hp["response_cost"]
        for k in ("router", "fusion", "annotations"):
            if hp.get(k) is not None:
                out[f"hidden_{k}"] = hp[k]
    return out


def record_response(role: str, plugin: dict, resp) -> None:
    """Append one deliberation row. Failure-isolated (never breaks a call)."""
    try:
        row = {
            "ts": _now_iso(),
            "role": role,
            "panel": list((plugin or {}).get("analysis_models") or []),
            "judge": (plugin or {}).get("model") or "(OpenRouter default)",
        }
        try:
            row.update(_extract(resp))
        except Exception:
            pass
        _append(json.dumps(row, default=str))
        # Meter the ACTUAL fusion spend against the per-day cap — accurate,
        # since the judge re-ingests every panel output (real cost ~10-20×, far
        # above a naive panel_size+1 estimate).
        cost = row.get("response_cost")
        if isinstance(cost, (int, float)) and cost > 0:
            try:
                from app.fusion import budget
                budget.record_spend(float(cost))
            except Exception:
                pass
    except Exception:
        pass


def _append(line: str) -> None:
    with _LOCK:
        try:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            existing: list[str] = []
            if _FILE.exists():
                existing = _FILE.read_text().splitlines()
            existing.append(line)
            if len(existing) > _CAP:
                existing = existing[-_CAP:]
            tmp = _FILE.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(existing) + "\n")
            tmp.replace(_FILE)
        except Exception:
            pass


def recent_deliberations(limit: int = 20) -> list[dict]:
    """Most-recent-first list of recorded deliberations."""
    try:
        if not _FILE.exists():
            return []
        lines = _FILE.read_text().splitlines()
        out: list[dict] = []
        for ln in reversed(lines):
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
            if len(out) >= max(1, int(limit)):
                break
        return out
    except Exception:
        return []
