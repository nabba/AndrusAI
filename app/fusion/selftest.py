"""Live OpenRouter Fusion self-test — operator-run, makes 1–2 PAID calls.

Confirms the one thing the host unit tests can't: that ``litellm`` forwards the
fusion ``extra_body.plugins`` + ``tool_choice`` to OpenRouter and the response
is usable — and prints the response *shape* so we know where the judge's
structured deliberation lands (Phase 2 persistence keys off this).

Run INSIDE the gateway (it has OPENROUTER_API_KEY + litellm + the live catalog),
after a rebuild that includes ``app/fusion/``::

    docker exec -i gateway python -m app.fusion.selftest             # 1 call (passthrough)
    docker exec -i gateway python -m app.fusion.selftest --factory   # +1 call (full factory hook)
    docker exec -i gateway python -m app.fusion.selftest --panel 3 --judge openrouter/anthropic/claude-opus-4.8

Cost: ~a few cents per call (small panel, ~80 tokens). Default 2-model panel.
Never imported by the package — only runs under ``__main__``.
"""

from __future__ import annotations

import argparse
import json
import sys


def _pick_panel(n: int) -> list[str]:
    """Resolve a panel via the real resolver; fall back to any catalog ids."""
    from app.fusion import config as C
    from app.fusion import panel as P

    members = P.resolve_panel(
        classes=C.panel_classes() or ["google", "qwen", "moonshotai", "deepseek"],
        pins=C.panel_pins(),
        hints=C.variant_hints(),
        max_panel=max(n, 2),
        blocked=C.blocked_models(),
    )
    if len(members) >= 2:
        return members[:n]
    from app.llm_catalog import CATALOG
    ids = [
        e["model_id"]
        for e in CATALOG.values()
        if e.get("provider") == "openrouter" and e.get("model_id") and not e.get("_retired")
    ]
    seen: set[str] = set()
    ids = [x for x in ids if not (x in seen or seen.add(x))]
    return ids[:n]


def _dump_shape(resp) -> None:
    """Print every plausible location the fusion deliberation might inhabit."""
    try:
        d = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    except Exception:
        d = {}
    print("  response top-level keys:", sorted(d.keys()))
    for path in ("router", "annotations", "provider"):
        if d.get(path) is not None:
            print(f"  META {path}:", json.dumps(d[path], default=str)[:400])
    try:
        msg = resp.choices[0].message
        print("  message.content:", repr(getattr(msg, "content", None))[:300])
        for attr in ("tool_calls", "annotations", "reasoning"):
            v = getattr(msg, attr, None)
            if v:
                rendered = v if isinstance(v, str) else json.dumps(v, default=str)
                print(f"  message.{attr}:", rendered[:400])
    except Exception as exc:
        print("  (could not read choices[0].message:", exc, ")")
    hp = getattr(resp, "_hidden_params", None)
    if isinstance(hp, dict):
        print("  _hidden_params keys:", sorted(hp.keys()))


def check_passthrough(panel_ids: list[str], judge: str | None) -> bool:
    import litellm

    plugin: dict = {"id": "fusion", "analysis_models": panel_ids}
    if judge:
        plugin["model"] = judge
    print(f"[A] passthrough: outer={panel_ids[0]} panel={panel_ids} judge={judge or '(default)'}")
    try:
        resp = litellm.completion(
            model=panel_ids[0],
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=80,
            extra_body={"plugins": [plugin]},
            tool_choice="required",
        )
        print("  PASS — litellm forwarded plugins+tool_choice; OpenRouter accepted it.")
        _dump_shape(resp)
        return True
    except Exception as exc:
        print("  FAIL —", type(exc).__name__, str(exc)[:500])
        print("  → if this is a 400/invalid-plugin error, switch apply.py to the")
        print("    model='openrouter/fusion' alias form instead of the plugin form.")
        return False


def check_factory(panel_ids: list[str]) -> bool:
    """Exercise the real chokepoint end-to-end, without persisting settings."""
    import app.fusion.budget as B
    import app.fusion.config as C
    from app.fusion import panel as P

    role = "research"
    saved = (C.is_enabled_for, P.resolve_panel, B.under_cap, C.brake_engaged)
    C.is_enabled_for = lambda r: r == role
    P.resolve_panel = lambda **kw: list(panel_ids)
    B.under_cap = lambda cap: True
    C.brake_engaged = lambda: False
    try:
        from app.llm_factory import chat_completion_for_role

        print(f"[B] factory hook: chat_completion_for_role({role!r}) with fusion forced ON")
        resp = chat_completion_for_role(role).create(
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=80,
        )
        print("  PASS — factory resolved a panel, injected fusion, returned a response.")
        _dump_shape(resp)
        return True
    except Exception as exc:
        print("  FAIL —", type(exc).__name__, str(exc)[:500])
        return False
    finally:
        C.is_enabled_for, P.resolve_panel, B.under_cap, C.brake_engaged = saved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Live OpenRouter Fusion self-test (PAID).")
    ap.add_argument("--panel", type=int, default=2, help="panel size (default 2)")
    ap.add_argument("--judge", default="", help="explicit judge model id (default: OpenRouter default)")
    ap.add_argument("--factory", action="store_true", help="also exercise the real factory hook (+1 paid call)")
    args = ap.parse_args(argv)

    panel_ids = _pick_panel(args.panel)
    if len(panel_ids) < 2:
        print("ABORT: fewer than 2 OpenRouter models resolvable from the catalog.", file=sys.stderr)
        return 2

    ok = check_passthrough(panel_ids, args.judge or None)
    if args.factory:
        ok = check_factory(panel_ids) and ok
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
