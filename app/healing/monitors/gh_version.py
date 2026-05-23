"""gh CLI version-drift monitor (Plan Risk #4 closure, 2026-05-22).

The change-request apply path
(:mod:`app.change_requests.apply._run_git_auto_pr`), the change-request
rollback path (:mod:`app.change_requests.apply._run_git_revert_pr`),
the coding-session branch-submit path
(:class:`app.coding_session.backends.BridgeWorktreeBackend.submit_as_branch`),
and the epistemic autotuner PR path
(:mod:`app.epistemic.autotune`) all rely on the HOST's ``gh`` CLI to
open pull requests against ``main``.

The gateway Dockerfile deliberately does NOT install ``gh`` — the
binary lives on the host, invoked via the bridge's ``/execute``
endpoint. That keeps the supply-chain surface small (gh authenticates
to GitHub; we don't want a GitHub token leaking into the container)
but it means version drift on the host is invisible until a PR
silently fails OR — worse — succeeds with subtly different behaviour
after an undocumented flag change.

What this monitor does
──────────────────────

  1. On each cycle, runs ``gh --version`` via the bridge with a
     5-second timeout and a working_dir of ``/`` (no repo state
     required — we just want the version output).
  2. Parses the leading ``gh version M.m.p`` line.
  3. If no baseline exists, records (M, m, p, observed_at,
     bridge_endpoint_hint) at
     ``workspace/healing/gh_version_baseline.json``. **No alert** —
     first observation just establishes the baseline.
  4. If the major version differs from baseline, sends a Signal
     alert (likely API break, operator should test PR creation
     before next deploy). Dedup: 14 days per (old_major, new_major).
  5. If only the minor/patch differs, quietly updates the baseline
     (those are bug-fix / additive releases per gh's semver promise).

What this monitor does NOT do
─────────────────────────────

  * Does NOT pin gh to a specific version. The Dockerfile pins
    things the system OWNS (python, chromadb, shinka). gh is the
    operator's tool; we observe drift rather than dictate version.
  * Does NOT install gh anywhere. Pure observation.
  * Does NOT alert when the bridge is unreachable. That's a separate
    failure mode that ``listener_heartbeat`` + ``signal_heartbeat``
    will surface; layering a third alert here would just amplify
    noise during an outage.
  * Does NOT alert on first-ever observation. The baseline IS the
    first observation.

Cadence: daily probe. Master switch: ``gh_version_monitor_enabled``
(default ON).
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Deferred import of the _common helpers ──────────────────────────
#
# Direct ``from app.healing.handlers._common import ...`` at module
# top pulls in the entire ``app.healing`` package via its
# ``__init__.py``, which transitively imports ``app.config``, which
# requires pydantic_settings. Pinning that dependency at boot is
# fragile in test environments without the package.
#
# Deferred resolution: helpers loaded once on first call, cached on
# the module's namespace. Tests can still monkeypatch the cached
# references — the tests in ``test_gh_version_monitor.py`` swap
# them out in an ``isolated_state`` fixture for state-redirection.
read_state_json: Any = None
write_state_json: Any = None
send_signal_alert: Any = None
audit_event: Any = None


def _resolve_common() -> None:
    """Resolve helpers from ``app.healing.handlers._common`` on first
    use. Cached on this module. Tests may pre-set these globals to
    fakes; the resolver respects existing non-None bindings."""
    global read_state_json, write_state_json, send_signal_alert
    global audit_event
    needs = (
        read_state_json is None
        or write_state_json is None
        or send_signal_alert is None
        or audit_event is None
    )
    if not needs:
        return
    try:
        from app.healing.handlers import _common
    except Exception:
        logger.debug(
            "gh_version: _common deferred import failed", exc_info=True,
        )
        # Install no-op fallbacks so the monitor degrades gracefully
        # rather than crashing the daemon thread.
        if read_state_json is None:
            read_state_json = lambda name, default=None: (
                default if default is not None else {}
            )
        if write_state_json is None:
            write_state_json = lambda name, payload: None
        if send_signal_alert is None:
            send_signal_alert = lambda text, *, tag="self_heal": False
        if audit_event is None:
            audit_event = lambda action, **detail: None
        return
    if read_state_json is None:
        read_state_json = _common.read_state_json
    if write_state_json is None:
        write_state_json = _common.write_state_json
    if send_signal_alert is None:
        send_signal_alert = _common.send_signal_alert
    if audit_event is None:
        audit_event = _common.audit_event


NAME = "gh_version"
CADENCE_SECONDS = 24 * 3600  # daily probe
MASTER_SWITCH_KEY = "gh_version_monitor_enabled"

_INTERNAL_CADENCE_S = 7 * 24 * 3600  # weekly actual probe
_DEDUP_WINDOW_S = 14 * 86400  # 14-day alert dedup
_STATE_FILE_NAME = "gh_version_baseline.json"

# Matches: "gh version 2.40.1 (2024-09-25)" and similar. We only
# parse the leading triple; the trailing date / build-id varies and
# isn't relevant to drift detection.
_GH_VERSION_RE = re.compile(
    r"^\s*gh\s+version\s+(\d+)\.(\d+)\.(\d+)", re.IGNORECASE,
)


def _enabled() -> bool:
    """Master-switch read.

    Precedence: env var ``GH_VERSION_MONITOR_ENABLED`` first (ops
    override for emergency on/off without restart — and the contract
    that test fixtures rely on), then runtime_settings (the React-
    toggleable persistent state), then default ON.
    """
    env = os.getenv("GH_VERSION_MONITOR_ENABLED", "")
    if env:
        return env.lower() in ("true", "1", "yes", "on")
    try:
        from app import runtime_settings
        if hasattr(runtime_settings, "get_gh_version_monitor_enabled"):
            return bool(
                runtime_settings.get_gh_version_monitor_enabled()
            )
    except Exception:
        logger.debug(
            "gh_version: runtime_settings read raised", exc_info=True,
        )
    return True


def _probe_gh_version(
    bridge_factory: Optional[Any] = None,
) -> tuple[Optional[tuple[int, int, int]], str]:
    """Run ``gh --version`` via the bridge.

    Returns ``((major, minor, patch), raw_first_line)`` on success,
    ``(None, reason)`` on any failure. The factory parameter exists
    for test injection — production calls pass None and we resolve
    via ``app.bridge_client.get_bridge`` lazily.
    """
    if bridge_factory is None:
        try:
            from app.bridge_client import get_bridge
        except Exception:
            return None, "bridge_client import failed"
        bridge_factory = lambda: get_bridge("change_requests")  # noqa: E731

    try:
        bridge = bridge_factory()
    except Exception as exc:
        logger.debug("gh_version: bridge factory raised: %s", exc)
        return None, f"bridge factory raised: {exc}"
    if bridge is None:
        return None, "bridge unavailable"
    if hasattr(bridge, "is_available") and not bridge.is_available():
        return None, "bridge not available"

    try:
        result = bridge.execute(
            ["gh", "--version"], working_dir="/", timeout=5,
        ) or {}
    except Exception as exc:
        logger.debug(
            "gh_version: bridge.execute raised: %s", exc, exc_info=True,
        )
        return None, f"bridge.execute raised: {exc}"

    rc = result.get("returncode", 0)
    if rc != 0:
        stderr = (result.get("stderr") or "").strip()[:200]
        return None, f"gh --version exit {rc}: {stderr or 'no stderr'}"

    stdout = (result.get("stdout") or "").strip()
    if not stdout:
        return None, "gh --version produced no stdout"

    first_line = stdout.splitlines()[0].strip()
    m = _GH_VERSION_RE.match(first_line)
    if not m:
        return None, f"unparseable gh --version output: {first_line!r}"
    triple = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return triple, first_line


def _format_version(v: tuple[int, int, int]) -> str:
    return f"{v[0]}.{v[1]}.{v[2]}"


def _now_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
        timespec="seconds",
    )


def run(
    *,
    now: Optional[float] = None,
    bridge_factory: Optional[Any] = None,
) -> dict[str, Any]:
    """Single-pass freshness probe. Returns a structured summary.

    Test/operator hooks:
      * ``now``: overrides ``time.time()`` for deterministic tests.
      * ``bridge_factory``: callable returning a bridge-like object
        with ``.execute(command, working_dir, timeout)``; injected
        in tests so we don't need a live bridge.
    """
    summary: dict[str, Any] = {
        "ran": False,
        "probe_ok": False,
        "version": None,
        "raw_line": None,
        "drift_kind": None,           # "first_baseline" | "major" | "minor_patch" | None
        "baseline_before": None,
        "alert_fired": False,
        "alert_tag": None,
        "skip_reason": None,
    }
    if not _enabled():
        summary["skip_reason"] = "monitor disabled"
        return summary

    _resolve_common()

    cur = float(now) if now is not None else time.time()

    state = read_state_json(_STATE_FILE_NAME, {
        "last_run_at": 0.0,
        "baseline": None,        # dict | None
        "last_alert_at": {},     # tag → ts
        "history": [],           # list of {ts, version, drift_kind}
    })

    # Cadence guard: skip if we ran within the internal cadence.
    # The driver's cadence is daily, but actual probing is weekly so
    # we don't burn 7× the bridge calls. First-ever invocation
    # (last_run == 0) always runs — that's how we establish the
    # baseline. Tests bypass on subsequent calls via ``now``.
    last_run = float(state.get("last_run_at", 0))
    if last_run > 0 and cur - last_run < _INTERNAL_CADENCE_S:
        summary["skip_reason"] = "within internal cadence window"
        return summary
    state["last_run_at"] = cur
    summary["ran"] = True

    triple, raw = _probe_gh_version(bridge_factory=bridge_factory)
    if triple is None:
        # Bridge unreachable / gh missing / unparseable output. We
        # SKIP cleanly — listener_heartbeat + signal_heartbeat cover
        # the "bridge died" case; we don't add a third alert.
        summary["skip_reason"] = raw
        # Don't persist last_run when the probe failed — that way
        # the next cadence retry actually fires. We DO surface the
        # skip reason in audit so operators can see the pattern.
        state["last_run_at"] = last_run
        write_state_json(_STATE_FILE_NAME, state)
        audit_event(
            "gh_version_skip",
            reason=raw[:200],
        )
        return summary

    summary["probe_ok"] = True
    summary["version"] = _format_version(triple)
    summary["raw_line"] = raw

    baseline = state.get("baseline")
    summary["baseline_before"] = (
        _format_version((
            int(baseline["major"]), int(baseline["minor"]),
            int(baseline["patch"]),
        ))
        if isinstance(baseline, dict)
        and all(k in baseline for k in ("major", "minor", "patch"))
        else None
    )

    def _record_baseline(drift_kind: str) -> None:
        state["baseline"] = {
            "major": triple[0],
            "minor": triple[1],
            "patch": triple[2],
            "raw": raw,
            "observed_at": _now_iso(cur),
        }
        history = state.setdefault("history", [])
        if not isinstance(history, list):
            history = []
            state["history"] = history
        history.append({
            "ts": _now_iso(cur),
            "version": _format_version(triple),
            "drift_kind": drift_kind,
        })
        # Cap history at 50 rows so the state file stays compact.
        if len(history) > 50:
            del history[: len(history) - 50]

    def _maybe_alert(tag: str, body: str) -> None:
        last_alerts = state.setdefault("last_alert_at", {})
        if not isinstance(last_alerts, dict):
            last_alerts = {}
            state["last_alert_at"] = last_alerts
        last = float(last_alerts.get(tag, 0))
        # First-time alert for this tag (last == 0) always fires.
        if last > 0 and cur - last < _DEDUP_WINDOW_S:
            return
        try:
            send_signal_alert(body, tag=tag)
        except Exception:
            logger.debug(
                "gh_version: send_signal_alert raised", exc_info=True,
            )
        last_alerts[tag] = cur
        summary["alert_fired"] = True
        summary["alert_tag"] = tag

    if baseline is None or not isinstance(baseline, dict):
        # First-ever observation — establish baseline, no alert.
        _record_baseline("first_baseline")
        summary["drift_kind"] = "first_baseline"
    else:
        try:
            old = (
                int(baseline["major"]),
                int(baseline["minor"]),
                int(baseline["patch"]),
            )
        except (KeyError, TypeError, ValueError):
            # Corrupt baseline — re-baseline silently. The audit row
            # records the recovery for operator visibility.
            _record_baseline("baseline_recovered")
            summary["drift_kind"] = "baseline_recovered"
            audit_event(
                "gh_version_baseline_recovered",
                new_version=_format_version(triple),
            )
        else:
            if triple == old:
                # No drift — leave baseline untouched.
                summary["drift_kind"] = None
            elif triple[0] != old[0]:
                # Major version drift — likely API break.
                old_str = _format_version(old)
                new_str = _format_version(triple)
                _maybe_alert(
                    f"gh_version:major:{old[0]}->{triple[0]}",
                    f"🛑 gh CLI MAJOR version drift on the host: "
                    f"{old_str} → {new_str}.\n"
                    f"This is likely an API-breaking release. Before "
                    f"the next CR apply / coding-session branch-submit / "
                    f"epistemic autotune PR, run a manual test:\n"
                    f"  bridge.execute(['gh', 'pr', 'create', "
                    f"'--help'])\n"
                    f"Baseline auto-updated; the new version is now "
                    f"the reference for drift detection.",
                )
                _record_baseline("major")
                summary["drift_kind"] = "major"
            else:
                # Minor or patch drift — additive per gh's semver
                # promise. Update baseline silently.
                _record_baseline("minor_patch")
                summary["drift_kind"] = "minor_patch"

    audit_event(
        "gh_version_pass",
        version=summary["version"],
        baseline_before=summary["baseline_before"],
        drift_kind=summary["drift_kind"],
        alert_fired=summary["alert_fired"],
        alert_tag=summary["alert_tag"],
    )

    write_state_json(_STATE_FILE_NAME, state)
    return summary
