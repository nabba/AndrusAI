"""Pinning test for the ``/api/cp/settings`` dispatcher (2026-05-23).

Background — what bug class this test catches
─────────────────────────────────────────────

On 2026-05-23 the operator discovered that the React
``UpgradeLifecycleCard`` and ``AbsencePolicyCard`` checkboxes at
``/cp/settings`` were cosmetic: every click POSTed
``{key: bool}`` to ``/api/cp/settings`` with a 200 OK response,
but the dispatcher in
``app/api/config_api.py:set_runtime_settings_endpoint`` had no
``if "<key>" in payload: set_<key>(...)`` branch for any of the
12 upgrade-lifecycle keys.

The setters existed in ``app/runtime_settings.py`` and the GET
endpoint returned the value, so the bug was completely silent:

    POST /api/cp/settings {"upgrade_lifecycle_enabled": true}
        → 200 OK
        (no dispatcher branch — silently dropped)

    GET /api/cp/settings
        → {"upgrade_lifecycle_enabled": false, …}
        (still the default — operator's click no-op'd)

After today's fix the 12 keys round-trip correctly. This test
pins the round-trip property so the same class of regression
cannot ship again.

Why React-side-of-truth (not runtime_settings-side)
───────────────────────────────────────────────────

You cannot walk ``def set_*`` in ``runtime_settings.py`` and
assert each one is dispatchable through ``/api/cp/settings`` —
some setters legitimately go through OTHER endpoints:

  * ``llm_mode``          → ``POST /llm_mode``
  * ``creative_run_budget_usd`` → ``POST /creative_mode``
  * ``background_tasks`` → ``POST /background_tasks``
  * ``governance ratchet`` → ``POST /governance_ratchet/set``

So the test extracts keys from the React side (``.tsx`` files
that POST to ``/api/cp/settings`` or the canonical
``/config/runtime_settings``) and round-trips each one.

How "known-failing" keys are handled
────────────────────────────────────

Six React settings cards (Connector Budget, Capability
Regression, Source Ledger, Task Recovery is OK but the source
ledger drills aren't, plus the RecentSubsystemsCard iterate/
benchmarks toggles) POST keys the dispatcher has never handled
— they're silently dropping today. Each is documented in
``KNOWN_SILENTLY_DROPPED_KEYS`` below.

The test pins TWO invariants:

  1. Every React-extracted key NOT in the known-dropped set
     MUST round-trip (any NEW silent drop fails CI).
  2. Every key IN the known-dropped set MUST currently drop
     AND MUST appear in the React-extracted set (fixing a
     bug without removing it from the list, or removing it
     from React without un-listing, fails CI).

This keeps the list honest: the operator can fix existing
bugs in a separate PR and the test will fail until the entry
is removed from ``KNOWN_SILENTLY_DROPPED_KEYS``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENTS_DIR = REPO_ROOT / "dashboard-react" / "src" / "components"


# ─── Known silent-drops (pre-existing on 2026-05-23) ──────────────────
#
# Each entry is a runtime-settings key that the React UI POSTs to
# ``/api/cp/settings`` but for which the dispatcher in
# ``app/api/config_api.py:set_runtime_settings_endpoint`` has NO
# ``if "<key>" in payload: set_<key>(…)`` branch. POSTing the key
# returns 200 OK with no state change; the operator's toggle no-ops.
#
# The pin: removing an entry from this set REQUIRES adding the
# dispatcher branch in the same PR (the round-trip test for the key
# will then fail until it actually persists).
#
# Adding an entry to this set is ONLY appropriate if a NEW React card
# is shipping in a known-incomplete state with operator-visible
# acknowledgement; default disposition is to add the dispatcher
# branch before merge.
KNOWN_SILENTLY_DROPPED_KEYS: set[str] = {
    # ConnectorBudgetCard.tsx — POSTs both keys; no dispatcher branch.
    # Both default to absent from snapshot so the bug is doubly
    # invisible (no value to read back at all).
    "connector_budgets_enabled",
    "connector_budget_overrides",
    # CapabilityRegressionCard.tsx — POSTs this key; no branch.
    # Absent from snapshot defaults.
    "capability_regression_enabled",
    # BenchmarksPage.tsx + RecentSubsystemsCard.tsx (via the
    # useUpdateRuntimeSettings hook) — POST both keys; no branch.
    "iterate_loop_enabled",
    "benchmarks_enabled",
    # SourceLedgerCard.tsx — 8 toggles, none of them dispatchable.
    "chromadb_source_ledger_enabled",
    "chromadb_ledger_bootstrap_enabled",
    "chromadb_ledger_drift_replay_enabled",
    "chromadb_ledger_compaction_enabled",
    "chromadb_ledger_s3_upload_enabled",
    "chromadb_ledger_gdrive_upload_enabled",
    "drill_source_ledger_replay_enabled",
    "drill_embedding_rotation_enabled",
    # SettingsPage.tsx cloud-hardening section — POSTs 3 keys;
    # setters exist (`set_hardening_profile`, `set_binauthz_mode`,
    # `set_gcp_bootstrap_enabled` in runtime_settings.py) but no
    # dispatcher branch. (Note: PROGRAM §57 ships a typed VITE_
    # bearer-secret path for these; the dispatcher gap is real.)
    "gcp_bootstrap_enabled",
    "hardening_profile",
    "binauthz_mode",
}


# ─── React-key extraction ─────────────────────────────────────────────


_POST_URL_PATTERNS = (
    "/api/cp/settings",
    "/api/cp/runtime_settings",
    "/config/runtime_settings",
)

# RecentSubsystemsCard.tsx doesn't have the URL inline — it uses the
# ``useUpdateRuntimeSettings`` mutation hook which targets the
# canonical ``/config/runtime_settings`` endpoint (which is the SAME
# dispatcher function as ``/api/cp/settings``). Whitelist by name.
_HOOK_FILE_BASENAMES = ("RecentSubsystemsCard.tsx",)


# Identifiers that LOOK like setting keys but aren't — auth-gate
# payload fields read by the dispatcher alongside the toggle.
_NOT_SETTINGS_KEYS = {
    "social_graph_confirm_phrase",
    "graph_suggestions_confirm_phrase",
}


# Object-key regex: ``identifier:`` not preceded by a ``.`` or ``?``
# (rules out method calls and optional-chains).
_KEY_RE = re.compile(r"(?<![\w.?])\b([a-z][a-z0-9_]+):", re.MULTILINE)


def _extract_object_literal_keys(text: str) -> set[str]:
    """Pluck identifier keys from object literals passed to any of:
    ``JSON.stringify({...})``, ``update({...})``, ``save({...})``,
    ``mutate({...})``, ``mutateAsync({...})``.

    Returns the *raw* set — caller filters against the snapshot to
    drop false positives (style fields, type-def keys, etc.).
    """
    keys: set[str] = set()

    # NOTE: ``[^{}]*`` matches a SHALLOW object body. Multi-line is
    # fine via DOTALL; nested ``{}`` is rare in these card patches
    # and the snapshot intersection catches false negatives.
    for fn in ("JSON\\.stringify", "update", "save", "mutate", "mutateAsync"):
        pat = re.compile(
            r"\b" + fn + r"\(\s*\{([^{}]*)\}", re.DOTALL,
        )
        for m in pat.finditer(text):
            for k in _KEY_RE.findall(m.group(1)):
                keys.add(k)

    # Template-literal computed keys: ``[`prefix${var}`]: v``.
    # VerificationExtensionCard uses this for the three zone
    # thresholds. Expand the placeholder explicitly.
    for m in re.finditer(r"\[`([a-z_]+)\$\{(\w+)\}`\]", text):
        prefix, var = m.group(1), m.group(2)
        if var == "zone":
            for z in ("chat", "autonomous", "financial"):
                keys.add(prefix + z)

    # ``key: 'IDENTIFIER'`` — used by UpgradeLifecycleCard's and
    # RecentSubsystemsCard's stageSwitches lists. The actual POST is
    # ``JSON.stringify({ [key]: value })`` with a computed key, so the
    # earlier extractor misses it. Capture the string value here.
    for m in re.finditer(
        r"""\bkey:\s*['"]([a-z][a-z0-9_]+)['"]""", text,
    ):
        keys.add(m.group(1))

    # Catch-all for string-literal settings keys passed as function
    # arguments (e.g. ``updateSwitch('upgrade_lifecycle_enabled', …)``
    # in UpgradeLifecycleCard's top-level master switch). Scope: any
    # snake_case string literal of length ≥ 8 that ends in a known
    # settings-key suffix. Snapshot intersection drops false positives.
    _SUFFIX_RE = re.compile(
        r"""['"](?P<id>[a-z][a-z0-9_]+(?:_enabled|_disabled|_url|_key|"""
        r"""_mode|_profile|_formula|_months|_overrides|_requestors|"""
        r"""_paths|_quarterly|_per_task|_cap_usd|_execute|_threshold"""
        r"""(?:_chat|_autonomous|_financial)?|_override|_floor|"""
        r"""_ceiling))['"]""",
    )
    for m in _SUFFIX_RE.finditer(text):
        keys.add(m.group("id"))

    return keys


def _walk_react_files() -> dict[str, set[str]]:
    """Return ``{basename: keys}`` for every .tsx that POSTs settings."""
    out: dict[str, set[str]] = {}
    for tsx in sorted(COMPONENTS_DIR.glob("*.tsx")):
        text = tsx.read_text(encoding="utf-8")
        targets_settings_endpoint = any(p in text for p in _POST_URL_PATTERNS)
        uses_mutation_hook = tsx.name in _HOOK_FILE_BASENAMES
        if not (targets_settings_endpoint or uses_mutation_hook):
            continue
        keys = _extract_object_literal_keys(text)
        # Drop confirmed non-settings identifiers.
        keys -= _NOT_SETTINGS_KEYS
        if keys:
            out[tsx.name] = keys
    return out


# ─── TestClient fixture (mini-app, no docker required) ────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Mini FastAPI app with ONLY the settings routers.

    Why mini-app vs ``app.main:app``: ``main.py`` runs gateway-side
    init at import (tool_supervisor, scheduler boot, healing
    daemons) that has no business firing during a unit test —
    several pieces require the full container env. The settings
    dispatcher only needs the two routers. Matches the pattern in
    ``tests/test_widening_decisions_and_api.py``.
    """
    # Dev mode auth pass-through; isolate writes to tmp_path.
    monkeypatch.setenv("GATEWAY_AUTH_REQUIRED", "0")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    # 5-per-minute rate limiter would block the multi-key round-trip
    # below. Stub it for the duration of the test. The limiter's
    # intent is to slow operator-pace flipping; it's not a
    # correctness invariant.
    from app.api import config_api as _config_api
    monkeypatch.setattr(_config_api, "_config_rate_check", lambda: True)

    # ``app.paths.WORKSPACE_ROOT`` is computed at module-import
    # time. If a sibling test ran first (the upgrade_lifecycle
    # suite does, for example), ``_STATE_PATH`` is already bound
    # to ``/app/workspace/runtime_settings.json`` which is
    # read-only on the host. Rebind directly + invalidate the
    # in-memory cache so the next ``_ensure_initialized()`` reads
    # tmp_path instead.
    from app import runtime_settings as _rs
    monkeypatch.setattr(_rs, "_STATE_PATH", tmp_path / "runtime_settings.json")
    monkeypatch.setattr(_rs, "_cache", None)
    # Identity-ledger continuity emissions on policy flips can
    # touch ``workspace/identity/`` too — silence them to keep
    # the test fully tmp_path-bound.
    try:
        from app.identity import continuity_ledger as _cl
        monkeypatch.setattr(_cl, "record_event", lambda *a, **kw: None)
    except Exception:
        pass

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.config_api import router as config_router
    from app.control_plane.settings_alias_api import router as alias_router

    test_app = FastAPI()
    test_app.include_router(config_router, prefix="/config")
    test_app.include_router(alias_router)
    return TestClient(test_app)


# ─── Round-trip value selection ───────────────────────────────────────


_SKIP = object()


# Keys that require a typed-phrase confirmation in the SAME payload
# when flipping False → True. We bundle the phrase so the test can
# round-trip them without hitting the 400-gate.
_TYPED_PHRASE_PAYLOADS = {
    "person_correlation_social_graph_enabled": {
        "social_graph_confirm_phrase": "ENABLE SOCIAL GRAPH",
    },
    "graph_suggestions_enabled": {
        "graph_suggestions_confirm_phrase": "ENABLE GRAPH-DRIVEN SUGGESTIONS",
    },
}


def _round_trip_value_for(key: str, current: Any) -> Any:
    """Return a value distinct from ``current`` that will pass the
    setter's validation. ``_SKIP`` means the test should not attempt
    a round-trip for this key (rare — only for keys with no safe
    alternate value).
    """
    # Booleans (and None-defaulted booleans treated as False) → flip.
    if isinstance(current, bool) or current is None:
        # current is bool OR (current is None and the React TS type
        # is boolean? — we can't tell from here, but None default →
        # send True is always a safe attempt; if the setter rejects
        # the test reports the rejection separately).
        return not bool(current)

    # Floats — keys with known validation ranges.
    if isinstance(current, float):
        # Threshold band has cross-coupled validation: floor <
        # ceiling, override in [floor, ceiling]. Pick values that
        # respect the defaults (0.50 / 0.95).
        if key == "structured_diagnosis_threshold_floor":
            return 0.10 if abs(current - 0.10) > 0.001 else 0.20
        if key == "structured_diagnosis_threshold_ceiling":
            return 0.99 if abs(current - 0.99) > 0.001 else 0.97
        if key == "structured_diagnosis_threshold_override":
            return 0.70 if abs(current - 0.70) > 0.001 else 0.72
        # Verification zones — [0.0, 1.0].
        if key.startswith("verification_threshold_"):
            return 0.50 if abs(current - 0.50) > 0.001 else 0.55
        # U5 quarterly budget — [0, 500].
        if key == "upgrade_lifecycle_capability_budget_usd_quarterly":
            return 50.0 if abs(current - 50.0) > 0.01 else 75.0
        # Vision CU monthly cap — non-negative float.
        if key == "vision_cu_monthly_cap_usd":
            return 15.0 if abs(current - 15.0) > 0.01 else 20.0
        # Default: bump by a small visible amount.
        return current + 1.0 if current < 100.0 else current - 1.0

    # Integers — pick a safe alternate.
    if isinstance(current, int) and not isinstance(current, bool):
        if key == "verification_extension_retrieval_budget_per_task":
            return 5 if current != 5 else 7
        if key == "person_correlation_decay_months":
            return 24 if current != 24 else 18
        if key == "executor_default_budget_tokens":
            return 100_000 if current != 100_000 else 150_000
        if key == "executor_default_wall_clock_s":
            return 300 if current != 300 else 600
        if key.startswith("widening_"):
            return current + 1
        return current + 1

    # Strings — special-case known formats.
    if isinstance(current, str):
        if key == "voice_mode":
            order = ("off", "local", "cloud")
            cur = (current or "").lower()
            for cand in order:
                if cand != cur:
                    return cand
            return "off"
        if key == "tripit_ical_url":
            # Empty is always valid; alternate is a tripit-hostname URL.
            if current:
                return ""
            return "https://www.tripit.com/feed/operator-test.ics"
        if key == "aviationstack_api_key":
            # Empty or len >= 16. Empty always valid.
            return "" if current else "a" * 32
        if key == "person_centrality_formula":
            order = ("frequency", "recency_weighted", "cross_modal")
            for cand in order:
                if cand != current:
                    return cand
            return "frequency"
        if key == "hardening_profile":
            order = ("off", "basic", "strict")
            for cand in order:
                if cand != current:
                    return cand
            return "strict"
        if key == "binauthz_mode":
            return "ENFORCE" if current != "ENFORCE" else "AUDIT"
        # Strings without a known safe alternate — skip rather than
        # guess and trip a setter's validation.
        return _SKIP

    # Lists — append + remove a marker.
    if isinstance(current, list):
        if key == "auto_apply_allowed_requestors":
            marker = "__pinning_test_marker__"
            return [*current, marker]
        if key == "auto_apply_allowed_paths":
            marker = "workspace/__pinning_test_marker__/"
            return [*current, marker]
        # Generic list: skip — we don't know the entry shape.
        return _SKIP

    # Dicts (e.g. connector_budget_overrides) — these legitimately
    # need a structured payload the dispatcher would have to validate.
    # The test scope is "does the dispatcher route the key at all";
    # we cover dicts via an empty-dict round-trip when feasible.
    if isinstance(current, dict):
        marker_key = "__pinning_test_marker__"
        new = dict(current)
        if marker_key in new:
            new.pop(marker_key)
        else:
            new[marker_key] = {"daily_cap_usd": 0.0}
        return new

    return _SKIP


def _values_equivalent(a: Any, b: Any) -> bool:
    """Compare GETted-back value to POSTed value tolerantly.

    Booleans match exactly. Floats within 1e-6. Lists order-
    insensitive. Dicts via ``==``.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return a == b
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(repr, a)) == sorted(map(repr, b))
    return a == b


# ─── Tests ────────────────────────────────────────────────────────────


def test_extractor_finds_react_keys():
    """Sanity: the React walker finds at least 10 cards posting
    settings keys. Catches the "extractor silently returns empty"
    failure mode (e.g. someone moves the components directory).
    """
    react = _walk_react_files()
    assert len(react) >= 10, (
        f"React extractor found only {len(react)} settings-card "
        f"files — moved or renamed? {sorted(react.keys())}"
    )
    # A few known-stable keys must appear:
    flat = set().union(*react.values())
    for must_have in (
        "upgrade_lifecycle_enabled",
        "shinka_inline_evolve_enabled",
        "resilience_drills_enabled",
        "person_correlation_enabled",
    ):
        assert must_have in flat, (
            f"extractor missed {must_have!r} — pattern may have drifted"
        )


def test_react_post_keys_round_trip_through_dispatcher(client):
    """The load-bearing test — every React-extracted key must
    round-trip POST → GET, except keys explicitly listed in
    ``KNOWN_SILENTLY_DROPPED_KEYS``.

    A failure means either:
      (a) A new React card POSTs a key the dispatcher doesn't
          handle (silent drop — the 2026-05-23 regression class).
      (b) The key's setter validation rejected our chosen
          round-trip value. Fix _round_trip_value_for() and add
          a special-case for the key.

    The failure message lists every dropped key so the operator
    can add the ``if "<key>" in payload:`` branch in one pass.
    """
    react = _walk_react_files()
    all_react_keys: set[str] = set()
    for keys in react.values():
        all_react_keys |= keys

    snapshot_before: dict[str, Any] = client.get("/api/cp/settings").json()

    # Filter to keys actually present in the runtime_settings
    # snapshot. Anything not there is either a false-positive from
    # the regex extractor (style keys, type-def keys, etc.) or a
    # key whose setter writes to a different store (the rare
    # exception — flagged by the consistency test below).
    relevant: list[str] = sorted(
        k for k in all_react_keys if k in snapshot_before
    )
    assert relevant, "no React keys overlap with snapshot — broken?"

    silent_drops: list[tuple[str, Any, Any]] = []
    other_failures: list[tuple[str, Any, str]] = []
    restore: dict[str, Any] = {}

    try:
        for key in relevant:
            original = snapshot_before[key]
            new_value = _round_trip_value_for(key, original)
            if new_value is _SKIP:
                continue
            restore[key] = original

            payload: dict[str, Any] = {key: new_value}
            # Bundle the typed-phrase confirmation for the two L4
            # gated keys when we're enabling them.
            if key in _TYPED_PHRASE_PAYLOADS and new_value is True:
                payload.update(_TYPED_PHRASE_PAYLOADS[key])

            resp = client.post("/api/cp/settings", json=payload)
            if resp.status_code == 400:
                # Validation rejection — the dispatcher routed the
                # key (otherwise it would have been a silent 200).
                # The round-trip value was just unacceptable.
                # Skip; not a silent-drop bug.
                continue
            if resp.status_code != 200:
                other_failures.append(
                    (key, new_value, f"POST → {resp.status_code} {resp.text[:200]}"),
                )
                continue

            after = client.get("/api/cp/settings").json().get(key)
            if not _values_equivalent(after, new_value):
                silent_drops.append((key, new_value, after))

    finally:
        # Best-effort restore — never raises even if dispatcher
        # is broken; just stays silent so the original assertion
        # message wins.
        for key, original in restore.items():
            try:
                payload: dict[str, Any] = {key: original}
                if key in _TYPED_PHRASE_PAYLOADS and original is True:
                    payload.update(_TYPED_PHRASE_PAYLOADS[key])
                client.post("/api/cp/settings", json=payload)
            except Exception:
                pass

    unexpected_drops = [
        (k, sent, got) for (k, sent, got) in silent_drops
        if k not in KNOWN_SILENTLY_DROPPED_KEYS
    ]
    assert not unexpected_drops, (
        "Dispatcher silently dropped these React POST keys (POST 200, "
        "value unchanged). Add an ``if \"<key>\" in payload:`` branch "
        "in app/api/config_api.py:set_runtime_settings_endpoint, OR — "
        "if this is intentional — add the key to "
        "KNOWN_SILENTLY_DROPPED_KEYS in this file with a TODO:\n"
        + "\n".join(
            f"  {k}: sent={sent!r} got_back={got!r}"
            for k, sent, got in unexpected_drops
        )
    )

    assert not other_failures, (
        "Non-silent-drop dispatch failures (unexpected status codes):\n"
        + "\n".join(
            f"  {k}: sent={sent!r} → {detail}"
            for k, sent, detail in other_failures
        )
    )


def _key_is_currently_dropping(client, key: str, snapshot: dict[str, Any]) -> bool:
    """Probe whether ``key`` is silently dropped by the dispatcher.

    Two cases:
      * Key IN snapshot — flip to a distinct value, GET back, check
        unchanged. (Round-trip-not-equal-to-sent == dropped.)
      * Key NOT in snapshot — POST a probe value, GET, check that
        the key is STILL absent or still None. (Setter would have
        added it to the snapshot when first written.)
    """
    if key in snapshot:
        original = snapshot[key]
        probe = _round_trip_value_for(key, original)
        if probe is _SKIP:
            return False  # can't probe; assume not dropping
        resp = client.post("/api/cp/settings", json={key: probe})
        if resp.status_code != 200:
            return False  # validation rejection → routed
        after = client.get("/api/cp/settings").json().get(key)
        dropped = not _values_equivalent(after, probe)
        # Restore.
        try:
            client.post("/api/cp/settings", json={key: original})
        except Exception:
            pass
        return dropped
    # Not in snapshot. Probe by writing True and checking the value
    # didn't appear OR remained None.
    resp = client.post("/api/cp/settings", json={key: True})
    if resp.status_code != 200:
        return False
    after_snap = client.get("/api/cp/settings").json()
    after_val = after_snap.get(key)
    # Dropped if value is still None or key still missing.
    return after_val is None


def test_known_silently_dropped_keys_list_stays_honest(client):
    """The ``KNOWN_SILENTLY_DROPPED_KEYS`` list must remain accurate:

      (a) Every listed key must currently drop. If the dispatcher
          gets fixed for the key without removing it from this
          set, the test fails — forcing the operator to either
          delete the entry (recommended) or document why.

      (b) Every listed key must appear in the React-extracted
          set. If the React card was deleted but the key stayed
          listed, the test fails.
    """
    react = _walk_react_files()
    all_react_keys: set[str] = set()
    for keys in react.values():
        all_react_keys |= keys

    snapshot_before: dict[str, Any] = client.get("/api/cp/settings").json()

    fixed_but_listed: list[str] = []
    not_in_react: list[str] = []

    for key in sorted(KNOWN_SILENTLY_DROPPED_KEYS):
        if key not in all_react_keys:
            not_in_react.append(key)
            continue
        if not _key_is_currently_dropping(client, key, snapshot_before):
            fixed_but_listed.append(key)

    msgs: list[str] = []
    if not_in_react:
        msgs.append(
            "These keys are listed as silently-dropped but NO React "
            "card POSTs them — remove from KNOWN_SILENTLY_DROPPED_KEYS:\n"
            + "\n".join(f"  - {k}" for k in not_in_react)
        )
    if fixed_but_listed:
        msgs.append(
            "These keys are listed as silently-dropped but actually "
            "round-trip successfully — remove from "
            "KNOWN_SILENTLY_DROPPED_KEYS (the bug is fixed):\n"
            + "\n".join(f"  - {k}" for k in fixed_but_listed)
        )

    assert not msgs, "\n\n".join(msgs)


def test_upgrade_lifecycle_dispatcher_keys_explicit_pin(client):
    """The 12 keys that triggered today's investigation get an
    explicit named pin — when one regresses the failure message
    points directly at the responsible PROGRAM §62 setter, not
    at the more abstract "extractor walked a card" framing.
    """
    keys = [
        # Master + 5 stage switches + apply-hook + 3 writers (today's add).
        "upgrade_lifecycle_enabled",
        "upgrade_lifecycle_capability_extraction_enabled",
        "upgrade_lifecycle_trial_enabled",
        "upgrade_lifecycle_major_auto_cr_enabled",
        "upgrade_lifecycle_capability_adoption_enabled",
        "ecosystem_snapshot_enabled",
        "upgrade_lifecycle_apply_hook_enabled",
        "upgrade_lifecycle_requirements_writer_enabled",
        "upgrade_lifecycle_dockerfile_writer_enabled",
        "upgrade_lifecycle_pyproject_writer_enabled",
        "upgrade_lifecycle_absence_policy_enabled",
        # Quarterly budget (the float setter, not a bool).
        "upgrade_lifecycle_capability_budget_usd_quarterly",
    ]
    snapshot_before = client.get("/api/cp/settings").json()
    restore: dict[str, Any] = {}
    try:
        for key in keys:
            assert key in snapshot_before, (
                f"{key}: present in setter inventory but missing from "
                "snapshot() — runtime_settings init drift"
            )
            original = snapshot_before[key]
            new_value = _round_trip_value_for(key, original)
            assert new_value is not _SKIP, f"no round-trip value for {key!r}"
            restore[key] = original
            resp = client.post("/api/cp/settings", json={key: new_value})
            assert resp.status_code == 200, (
                f"{key}: POST → {resp.status_code} {resp.text[:200]}"
            )
            after = client.get("/api/cp/settings").json()[key]
            assert _values_equivalent(after, new_value), (
                f"REGRESSION: {key} silently dropped by dispatcher — "
                f"the PROGRAM §62 fix from 2026-05-23 needs re-applying "
                f"to app/api/config_api.py:set_runtime_settings_endpoint "
                f"(look for the '§62' marker). sent={new_value!r} "
                f"got_back={after!r}"
            )
    finally:
        for key, original in restore.items():
            try:
                client.post("/api/cp/settings", json={key: original})
            except Exception:
                pass


def test_validation_rejection_returns_400(client):
    """The dispatcher's setters raise ``ValueError`` on out-of-range
    input; ``set_runtime_settings_endpoint`` catches those and
    re-raises as HTTP 400. Pinning the cleanest example: the U5
    quarterly budget cap (>$500 is rejected).
    """
    snapshot_before = client.get("/api/cp/settings").json()
    original = snapshot_before["upgrade_lifecycle_capability_budget_usd_quarterly"]

    # Above the cap → 400.
    resp = client.post(
        "/api/cp/settings",
        json={"upgrade_lifecycle_capability_budget_usd_quarterly": 1000.0},
    )
    assert resp.status_code == 400, (
        "expected 400 for budget > $500, got "
        f"{resp.status_code} {resp.text[:200]}"
    )

    # Negative → 400.
    resp = client.post(
        "/api/cp/settings",
        json={"upgrade_lifecycle_capability_budget_usd_quarterly": -5.0},
    )
    assert resp.status_code == 400, (
        "expected 400 for negative budget, got "
        f"{resp.status_code} {resp.text[:200]}"
    )

    # In-range → 200, value persists.
    resp = client.post(
        "/api/cp/settings",
        json={"upgrade_lifecycle_capability_budget_usd_quarterly": 42.5},
    )
    assert resp.status_code == 200
    assert abs(
        client.get("/api/cp/settings").json()[
            "upgrade_lifecycle_capability_budget_usd_quarterly"
        ] - 42.5
    ) < 1e-6

    # Cleanup — restore original.
    client.post(
        "/api/cp/settings",
        json={"upgrade_lifecycle_capability_budget_usd_quarterly": float(original)},
    )


def test_goodhart_hard_gate_safety_round_trip(client):
    """The Goodhart-hard-gate two-bool surface is safety-critical
    (one bool disables the gate, the other puts it in enforcing
    mode). Pin the round-trip — and make absolutely sure we
    restore the original values on exit, even if assertions fail.
    """
    snapshot_before = client.get("/api/cp/settings").json()
    orig_disabled = bool(snapshot_before.get("goodhart_hard_gate_disabled", False))
    orig_enforcing = bool(snapshot_before.get("goodhart_hard_gate_enforcing", False))

    try:
        # Round-trip ``disabled``.
        resp = client.post(
            "/api/cp/settings",
            json={"goodhart_hard_gate_disabled": not orig_disabled},
        )
        assert resp.status_code == 200
        after = client.get("/api/cp/settings").json()
        assert bool(after["goodhart_hard_gate_disabled"]) == (not orig_disabled)

        # Round-trip ``enforcing``.
        resp = client.post(
            "/api/cp/settings",
            json={"goodhart_hard_gate_enforcing": not orig_enforcing},
        )
        assert resp.status_code == 200
        after = client.get("/api/cp/settings").json()
        assert bool(after["goodhart_hard_gate_enforcing"]) == (not orig_enforcing)
    finally:
        # Always restore — even if the assertions above failed, the
        # gate must end in its operator-chosen state. The test fixture
        # uses tmp_path so this is theoretically isolated, but the
        # try/finally is the load-bearing safety pattern: copy it to
        # any future Goodhart-touching test.
        client.post(
            "/api/cp/settings",
            json={
                "goodhart_hard_gate_disabled": orig_disabled,
                "goodhart_hard_gate_enforcing": orig_enforcing,
            },
        )


# ─── Stretch: GET-side switches dict pinning ──────────────────────────


def test_upgrade_lifecycle_state_switches_are_all_dispatchable(client):
    """Stretch goal: walk the ``switches`` dict returned by
    ``GET /api/cp/upgrade-lifecycle/state`` and assert every key
    there round-trips through ``/api/cp/settings``.

    Catches the GET-side half of today's bug: on 2026-05-23 the
    operator added 4 keys to the GET state.switches dict
    (apply_hook, requirements_writer, dockerfile_writer,
    pyproject_writer) AT THE SAME TIME as adding the POST
    branches. If a future operator adds a switch to the GET dict
    but forgets the POST branch, this test catches it.

    Implementation: the upgrade-lifecycle router needs a few
    extra imports beyond the bare config router used elsewhere
    in this file. Build a second mini-app for this test alone.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.config_api import router as config_router
    from app.control_plane.settings_alias_api import router as alias_router

    # Try to wire the upgrade-lifecycle router; if its import
    # chain fails on this host (it pulls in postgres clients
    # under some paths), skip the test rather than fail.
    try:
        from app.control_plane.upgrade_lifecycle_api import (
            router as ul_router,
        )
    except ImportError as exc:
        pytest.skip(f"upgrade_lifecycle_api unavailable: {exc}")

    test_app = FastAPI()
    test_app.include_router(config_router, prefix="/config")
    test_app.include_router(alias_router)
    test_app.include_router(ul_router, prefix="/api/cp")
    c = TestClient(test_app)

    resp = c.get("/api/cp/upgrade-lifecycle/state")
    if resp.status_code != 200:
        pytest.skip(
            f"upgrade-lifecycle/state endpoint returned "
            f"{resp.status_code} on this host: {resp.text[:200]}"
        )
    state = resp.json()
    switches = state.get("switches") or {}
    assert switches, "upgrade-lifecycle GET state has empty 'switches'"

    snapshot_before = c.get("/api/cp/settings").json()
    restore: dict[str, Any] = {}
    dropped: list[tuple[str, Any, Any]] = []

    try:
        for key, current in sorted(switches.items()):
            assert key in snapshot_before, (
                f"{key} appears in upgrade-lifecycle state.switches "
                f"but not in /api/cp/settings snapshot — "
                "runtime_settings init drift"
            )
            new_value = _round_trip_value_for(key, current)
            if new_value is _SKIP:
                continue
            restore[key] = snapshot_before[key]
            resp = c.post("/api/cp/settings", json={key: new_value})
            if resp.status_code != 200:
                # Validation rejection — dispatcher routed it; fine.
                continue
            after = c.get("/api/cp/settings").json().get(key)
            if not _values_equivalent(after, new_value):
                dropped.append((key, new_value, after))
    finally:
        for key, original in restore.items():
            try:
                c.post("/api/cp/settings", json={key: original})
            except Exception:
                pass

    assert not dropped, (
        "Keys present in GET /api/cp/upgrade-lifecycle/state's "
        "'switches' dict but silently dropped by /api/cp/settings "
        "POST — the GET-side half of the 2026-05-23 bug class. "
        "Add the dispatch branches in app/api/config_api.py:\n"
        + "\n".join(
            f"  {k}: sent={s!r} got_back={g!r}" for (k, s, g) in dropped
        )
    )
