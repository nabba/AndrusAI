"""Model-class → concrete OpenRouter id resolution — the "LLM chooser" that
populates the fusion panel.

A *class* is a vendor family (``google`` / ``qwen`` / ``moonshotai`` /
``deepseek``). We deliberately do NOT hardcode model ids: for each class we
filter the live :data:`app.llm_catalog.CATALOG` to that vendor's OpenRouter
entries and pick the current champion. When Google ships a new Gemini Flash,
or DeepSeek a new version, the champion updates automatically — matching the
project's "factory is authoritative, no hardcoded ids" stance.

Vendor is the second path segment of ``model_id`` (``openrouter/<vendor>/<slug>``).
An operator-friendly alias map lets the UI accept "gemini"/"kimi" and resolve
them to the catalog's actual vendor segment.
"""

from __future__ import annotations

from app.llm_catalog import CATALOG

# Operator-friendly class names → catalog vendor segment.
_CLASS_ALIASES: dict[str, str] = {
    "gemini": "google",
    "kimi": "moonshotai",
    "moonshot": "moonshotai",
    "qwen3": "qwen",
    "claude": "anthropic",
    "gpt": "openai",
    "llama": "meta-llama",
    "mistral": "mistralai",
}

# Local tier rank (decoupled from llm_catalog internals on purpose).
_TIER_RANK: dict[str, int] = {
    "local": 0, "free": 1, "budget": 2, "mid": 3, "premium": 4,
}


def _vendor_of(model_id: str) -> str:
    """Return the vendor segment of an ``openrouter/<vendor>/<slug>`` id."""
    parts = (model_id or "").split("/")
    return parts[1].lower() if len(parts) >= 3 else ""


def _norm_class(cls: str) -> str:
    c = (cls or "").strip().lower()
    return _CLASS_ALIASES.get(c, c)


def champion_for_class(
    cls: str,
    hint: str = "",
    blocked: frozenset[str] | set[str] = frozenset(),
) -> str | None:
    """Resolve a vendor *class* to the best current OpenRouter model id.

    Selection: filter to live, non-retired, non-blocked OpenRouter entries
    of the vendor; if a ``hint`` is given prefer ids whose slug contains it
    (e.g. ``"flash"``); then rank by (tier, general-strength, cheaper-input).
    Returns ``None`` when the catalog has no matching entry yet.
    """
    vendor = _norm_class(cls)
    if not vendor:
        return None
    cands: list[tuple[str, dict, str]] = []
    for key, entry in CATALOG.items():
        if entry.get("provider") != "openrouter" or entry.get("_retired"):
            continue
        mid = entry.get("model_id", "")
        if not mid or mid in blocked or key in blocked:
            continue
        if _vendor_of(mid) != vendor:
            continue
        cands.append((key, entry, mid))
    if not cands:
        return None

    h = (hint or "").strip().lower()
    if h:
        hinted = [c for c in cands if h in c[2].lower()]
        if hinted:
            cands = hinted

    def _score(c: tuple[str, dict, str]) -> tuple[int, float, float]:
        _, entry, _mid = c
        return (
            _TIER_RANK.get(entry.get("tier", ""), 0),
            float((entry.get("strengths") or {}).get("general", 0.0) or 0.0),
            -float(entry.get("cost_input_per_m", 0.0) or 0.0),
        )

    cands.sort(key=_score, reverse=True)
    return cands[0][2]


def resolve_panel(
    classes: list[str],
    pins: dict[str, str] | None = None,
    hints: dict[str, str] | None = None,
    max_panel: int = 4,
    blocked: frozenset[str] | set[str] = frozenset(),
) -> list[str]:
    """Resolve the ordered list of concrete OpenRouter ids for the panel.

    Pins win over auto-resolution; unresolved/duplicate slots are dropped.
    Bounded to ``max_panel`` (OpenRouter accepts 1–8).
    """
    pins = pins or {}
    hints = hints or {}
    cap = max(1, min(8, int(max_panel or 4)))
    out: list[str] = []
    for cls in list(classes)[:cap]:
        pin = pins.get(cls)
        mid = str(pin).strip() if pin else champion_for_class(
            cls, hints.get(cls, ""), blocked,
        )
        if mid and mid not in out:
            out.append(mid)
    return out


def resolve_judge(judge_id: str = "") -> str | None:
    """Explicit judge id, or ``None`` to let OpenRouter default it.

    OpenRouter's documented default judge is a strong reasoning model
    (Claude Opus class), so omitting ``model`` from the plugin is a sane,
    version-agnostic default.
    """
    jid = (judge_id or "").strip()
    return jid or None
