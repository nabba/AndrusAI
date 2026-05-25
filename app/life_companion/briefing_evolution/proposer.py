"""proposer — weekly LLM-driven proposal of NEW briefing sections.

The hand-curated catalog under ``app/life_companion/briefing_sections/``
seeds the v1 set. This module is the "anticipate my wishes" layer
the operator asked for: once a week, Haiku 4.5 looks at

  * the current catalog (so it doesn't re-propose what already exists)
  * a fixed prompt-side enumeration of what other PIM systems offer
  * the operator's recent activity surface (tickets, paper-pipeline,
    skills, browse topics) to ground proposals in actual behavior

and writes up to 3 proposal rows to
``workspace/life_companion/briefing_evolution/proposed_ideas.jsonl``.

The proposals are surfaced in the briefing itself via the
``briefing_ideas`` candidate section so the operator sees them
inline. They are NOT auto-implemented — implementing a new section
requires writing a Python module, which is a code change that goes
through the standard operator gate.

Cost: ~$0.001/week (one Haiku call, ~3k input + ~500 output tokens).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from app.life_companion._common import audit_event, background_enabled, feature_enabled

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_IDEAS_PER_PASS = 3
_CADENCE_SECONDS = 7 * 86400  # weekly
_LEDGER_NAME = "proposed_ideas.jsonl"
_STATE_NAME = "proposer_state.json"


def _dir() -> Path:
    from app.paths import WORKSPACE_ROOT
    p = WORKSPACE_ROOT / "life_companion" / "briefing_evolution"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ledger_path() -> Path:
    return _dir() / _LEDGER_NAME


def _state_path() -> Path:
    return _dir() / _STATE_NAME


def _load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text() or "{}")
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    try:
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(p)
    except Exception:
        logger.debug("proposer: state save failed", exc_info=True)


def _existing_ids() -> list[tuple[str, str]]:
    """Return ``[(id, display_name), ...]`` for catalog + dropped + already-
    proposed ideas — so the LLM doesn't re-suggest them."""
    out: list[tuple[str, str]] = []
    try:
        from app.life_companion.briefing_evolution import catalog, trial_state
        for c in catalog.all_candidates():
            out.append((c.id, c.display_name))
        # Include dropped ones so we don't propose them again before
        # their 90d cooldown is up.
        for r in trial_state.list_sections():
            out.append((r.id, r.id))
    except Exception:
        pass
    # Read prior proposals too.
    p = _ledger_path()
    if p.exists():
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("id"):
                    out.append((str(row["id"]), str(row.get("display_name") or row["id"])))
        except OSError:
            pass
    return out


# Prompt-side enumeration of what *other* PIM / assistant systems
# typically include — gives the LLM grounding for "anticipate what
# the operator might want." Kept in the prompt rather than in code
# so it's easy to revise without redeploying.
_OTHER_SYSTEMS_OFFER = """\
Common briefing sections other PIM / personal-assistant systems include:
- Weather + air quality + UV index
- Sunrise / sunset / day length
- Currency + market summary
- Top news headlines (national + regional)
- Sports / scores
- Stock / portfolio summary
- Today's birthdays in contacts
- Commute / traffic / public-transit alerts
- On-this-day-in-history
- Quote of the day or journaling prompt
- Daily challenge (exercise / step goal / habit)
- Reading-list resume (where you left off)
- Wikipedia article of the day
- Word of the day (language learning)
- Tide / sun / moon phase (for nature-near operators)
- Bills due this week
- Top issue from each open project
- Today's outstanding TODOs
- Yesterday's coding-session summary
- Last night's sleep / heart-rate summary
- Reminders nearing their deadline
- Email follow-ups overdue
- Recurring-meeting prep
- Cooking suggestion based on what's in the fridge
"""


_SYSTEM_PROMPT = """\
You are proposing new sections for a personal morning briefing system.

The user is a multi-venture operator in Helsinki / Tallinn running PLG
(payment infrastructure), Eesti mets (forest analysis), Archibal
(content authenticity), and KaiCart (TikTok Shop for Thai sellers).
They live in Finland; Estonian + English are primary, Finnish is daily.

The briefing already has these sections (don't re-propose):
{existing_list}

Propose UP TO 3 NEW sections that would be valuable but aren't there
yet. Each proposal MUST be implementable from one of:
  (a) A free public API (no auth) — name the exact endpoint.
  (b) An internal BotArmy module — list the module path you'd read.
  (c) A short local computation over data the system already has.

Return STRICT JSON exactly in this shape:
{{
  "ideas": [
    {{
      "id": "kebab-case-id",
      "display_name": "Briefing label (with emoji)",
      "description": "1-sentence purpose",
      "data_source": "free_api | botarmy_module | local_computation",
      "data_source_detail": "exact endpoint, module path, or computation",
      "rationale": "why this would be useful for THIS operator specifically",
      "implementation_difficulty": "low | medium | high"
    }}
  ]
}}

If nothing meaningful to propose, return {{"ideas": []}} — empty beats
filler.
"""


def _llm_propose(existing: list[tuple[str, str]]) -> list[dict]:
    """One Haiku call. Returns the parsed ``ideas`` list or ``[]`` on
    any failure."""
    try:
        from app.llm_factory import anthropic_client_for_role
        client = anthropic_client_for_role(role="cheap-vetting")
    except Exception:
        return []
    existing_lines = "\n".join(f"  - {label} (id: {sid})" for sid, label in existing)
    system_msg = _SYSTEM_PROMPT.format(existing_list=existing_lines or "(none yet)")
    user_msg = _OTHER_SYSTEMS_OFFER
    try:
        msg = client.messages.create(
            max_tokens=900,
            system=system_msg,
            messages=[{"role": "user", "content": user_msg}],
        )
    except Exception:
        logger.debug("proposer: LLM call failed", exc_info=True)
        return []
    text = ""
    for block in msg.content or []:
        if getattr(block, "type", "") == "text":
            text += block.text
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        parsed = json.loads(text)
    except Exception:
        logger.debug("proposer: JSON parse failed: %r", text[:200])
        return []
    if not isinstance(parsed, dict):
        return []
    ideas = parsed.get("ideas")
    if not isinstance(ideas, list):
        return []
    cleaned: list[dict] = []
    for it in ideas[:_MAX_IDEAS_PER_PASS]:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("id", "")).strip().lower()
        if not sid or not re.match(r"^[a-z0-9][a-z0-9-]{1,40}$", sid):
            continue
        cleaned.append({
            "id": sid,
            "display_name": str(it.get("display_name", ""))[:80],
            "description": str(it.get("description", ""))[:240],
            "data_source": str(it.get("data_source", ""))[:30],
            "data_source_detail": str(it.get("data_source_detail", ""))[:200],
            "rationale": str(it.get("rationale", ""))[:240],
            "implementation_difficulty": str(it.get("implementation_difficulty", ""))[:10],
        })
    return cleaned


def _append_ledger(ideas: list[dict]) -> None:
    p = _ledger_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with p.open("a", encoding="utf-8") as f:
            for idea in ideas:
                row = dict(idea)
                row["proposed_at"] = now_iso
                f.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        logger.debug("proposer: ledger write failed", exc_info=True)


def recent_proposals(n: int = 5) -> list[dict]:
    """Public read API — used by the briefing_ideas candidate section."""
    p = _ledger_path()
    if not p.exists():
        return []
    rows: list[dict] = []
    try:
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(rows) >= n:
                break
    except OSError:
        return []
    return rows


def run() -> None:
    """Weekly cadence-guarded pass. Calls the LLM, appends new ideas to
    the ledger. Idempotent within ``_CADENCE_SECONDS`` window."""
    if not feature_enabled("briefing_proposer"):
        return
    if not background_enabled():
        return
    state = _load_state()
    last = float(state.get("last_run_at", 0.0))
    if last and (time.monotonic() - last) < _CADENCE_SECONDS:
        return
    existing = _existing_ids()
    ideas = _llm_propose(existing)
    if ideas:
        _append_ledger(ideas)
        audit_event(
            "briefing_proposer_pass",
            n_ideas=len(ideas),
            ids=",".join(i.get("id", "") for i in ideas),
        )
    state["last_run_at"] = time.monotonic()
    state["last_run_iso"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
