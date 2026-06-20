"""
Host-side watchdog for the BotArmy gateway container.

The gateway runs background work on the same event loop that serves HTTP,
so a heavy idle-job burst (training scorer, ONNX download, sentience probes)
can starve /signal/inbound for minutes after boot. The forwarder drops
messages whose retry budget elapses inside that window. The in-container
healing layer can't recover from a hung event loop because it lives inside
the same process.

This watchdog runs on the host, polls /health, and restarts the gateway
container when it stays unresponsive past a threshold. It also alerts the
operator via signal-cli directly (bypassing the gateway, since by definition
that's the thing that's broken).

It ALSO guards the Signal forwarder LaunchAgent (2026-06-19). The forwarder
(`signal/forwarder.py`, run by `com.botarmy.signal-forwarder`) is the single
point of failure for ALL inbound Signal: signal-cli downloads + acks messages
(so the user sees ✓✓) but nothing reaches the gateway until the forwarder POSTs
them. Its plist has KeepAlive=true, which restarts it after a CRASH — but
KeepAlive cannot resurrect a job that has been `launchctl bootout`'d (fully
unloaded), e.g. by a diagnostic "pause the forwarder" probe whose restore step
was skipped. That left inbound Signal silently dead for ~19h on 2026-06-19. The
watchdog now checks each poll that the forwarder is loaded and re-bootstraps it
if not (idempotent — a no-op while it's loaded; KeepAlive still owns crashes).

Environment:
    HEALTH_URL              gateway endpoint to poll (default http://127.0.0.1:8765/health)
    POLL_INTERVAL_SECONDS   gap between probes (default 20)
    HEALTH_TIMEOUT_SECONDS  per-probe timeout (default 5)
    FAILURE_THRESHOLD       consecutive failures before restart (default 6 → ~2 min)
    RESTART_COOLDOWN_SECONDS  refuse a second restart inside this window (default 300)
    RESTART_GRACE_SECONDS   skip probes for this long after restart kicks off (default 120)
    BOOT_GRACE_SECONDS      skip probes for this long after the WATCHDOG itself
                            starts, so a gateway that is mid-boot when the
                            watchdog (re)launches isn't killed before uvicorn
                            has bound the port (default 180). /health does not
                            answer until the gateway's lifespan startup fully
                            completes, so a cold boot legitimately looks
                            "unresponsive" for a stretch — this grace prevents
                            the watchdog from turning a slow boot into a
                            restart loop.
    GATEWAY_LIVENESS_PATH   process-liveness heartbeat file the gateway writes
                            from a daemon thread (default
                            <COMPOSE_PROJECT_DIR>/workspace/healing/gateway_liveness).
                            A fresh heartbeat means the PROCESS is alive even if
                            the event loop is too busy to answer /health.
    HEARTBEAT_STALE_SECONDS heartbeat age past which the process is treated as
                            wedged/dead → restart (default 60)
    HEALTH_ESCALATE_SECONDS how long /health may stay down WHILE the heartbeat
                            is fresh before we treat it as a real event-loop
                            wedge and restart anyway (default 900)
    COMPOSE_PROJECT_DIR     dir containing docker-compose.yml (default /Users/andrus/BotArmy/crewai-team)
    GATEWAY_SERVICE         compose service name (default gateway)
    SIGNAL_CLI_HTTP_URL     signal-cli JSON-RPC endpoint (default http://127.0.0.1:7583)
    SIGNAL_OWNER_NUMBER     recipient for watchdog alerts (required for alerts to fire)
    DOCKER_BIN              docker binary path (default /usr/local/bin/docker)
    LOG_PATH                file to mirror stdout into (default /tmp/gateway-watchdog.log)
    STATE_PATH              JSON file persisting cooldown state across watchdog
                            crashes (default ~/.crewai-bridge/gateway_watchdog_state.json)
    FORWARDER_GUARD_ENABLED whether to re-bootstrap the forwarder LaunchAgent if
                            it's found unloaded (default 1; set 0 to disable)
    FORWARDER_LABEL         launchd label of the forwarder agent
                            (default com.botarmy.signal-forwarder)
    FORWARDER_PLIST         path to the forwarder plist used for re-bootstrap
                            (default ~/Library/LaunchAgents/<label>.plist)
    FORWARDER_BOOTSTRAP_COOLDOWN_SECONDS  min gap between bootstrap attempts so a
                            forwarder that keeps dying doesn't thrash launchctl /
                            spam alerts (default 300). Recovery in the normal
                            unload-and-stay-down case is still one poll interval.
    LAUNCHCTL_BIN           launchctl binary path (default /bin/launchctl)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Optional

import requests

HEALTH_URL = os.environ.get("HEALTH_URL", "http://127.0.0.1:8765/health")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL_SECONDS", "20"))
HEALTH_TIMEOUT = float(os.environ.get("HEALTH_TIMEOUT_SECONDS", "15"))
FAILURE_THRESHOLD = int(os.environ.get("FAILURE_THRESHOLD", "6"))
RESTART_COOLDOWN = float(os.environ.get("RESTART_COOLDOWN_SECONDS", "300"))
RESTART_GRACE = float(os.environ.get("RESTART_GRACE_SECONDS", "120"))
BOOT_GRACE = float(os.environ.get("BOOT_GRACE_SECONDS", "180"))
COMPOSE_PROJECT_DIR = os.environ.get(
    "COMPOSE_PROJECT_DIR", "/Users/andrus/BotArmy/crewai-team"
)
GATEWAY_SERVICE = os.environ.get("GATEWAY_SERVICE", "gateway")
SIGNAL_CLI_URL = os.environ.get("SIGNAL_CLI_HTTP_URL", "http://127.0.0.1:7583")
SIGNAL_OWNER = os.environ.get("SIGNAL_OWNER_NUMBER", "")
DOCKER_BIN = os.environ.get("DOCKER_BIN", "/usr/local/bin/docker")
LOG_PATH = os.environ.get("LOG_PATH", "/tmp/gateway-watchdog.log")
STATE_PATH = os.environ.get(
    "STATE_PATH",
    os.path.expanduser("~/.crewai-bridge/gateway_watchdog_state.json"),
)

# ── Signal-forwarder guard (2026-06-19) ──────────────────────────────────
# The forwarder LaunchAgent is the single point of failure for inbound Signal.
# KeepAlive=true in its plist handles crashes, but a `launchctl bootout` (the
# diagnostic pause-probe) fully unloads it and KeepAlive cannot bring that back.
# The watchdog re-bootstraps it if it's found unloaded.
FORWARDER_GUARD_ENABLED = os.environ.get("FORWARDER_GUARD_ENABLED", "1") not in (
    "0", "false", "False", "",
)
FORWARDER_LABEL = os.environ.get("FORWARDER_LABEL", "com.botarmy.signal-forwarder")
FORWARDER_PLIST = os.environ.get(
    "FORWARDER_PLIST",
    os.path.expanduser(f"~/Library/LaunchAgents/{FORWARDER_LABEL}.plist"),
)
FORWARDER_BOOTSTRAP_COOLDOWN = float(
    os.environ.get("FORWARDER_BOOTSTRAP_COOLDOWN_SECONDS", "300")
)
LAUNCHCTL_BIN = os.environ.get("LAUNCHCTL_BIN", "/bin/launchctl")

# ── Agent-keeper guard (2026-06-19) ───────────────────────────────────────
# The keeper (org.andrus.botarmy.agent-keeper, a StartInterval LaunchAgent) is
# what keeps THIS watchdog — and every other botarmy agent — loaded. Guarding
# the keeper here, while the keeper guards the watchdog, makes the two mutually
# protective: no single `launchctl bootout` can leave both recovery agents dead.
KEEPER_GUARD_ENABLED = os.environ.get("KEEPER_GUARD_ENABLED", "1") not in (
    "0", "false", "False", "",
)
KEEPER_LABEL = os.environ.get("KEEPER_LABEL", "org.andrus.botarmy.agent-keeper")
KEEPER_PLIST = os.environ.get(
    "KEEPER_PLIST",
    os.path.expanduser(f"~/Library/LaunchAgents/{KEEPER_LABEL}.plist"),
)

# ── Process-liveness heartbeat gate (2026-06-01) ─────────────────────────
# The gateway writes a heartbeat file (app/liveness.py) from a daemon thread
# that survives event-loop starvation. When /health is unresponsive we read
# this file to distinguish "process alive, loop busy" (a heavy idle job or a
# multi-minute evolver wait — DON'T restart, let it finish) from "process
# dead/wedged" (restart). This ends the loop where a busy-but-healthy gateway
# was guillotined at the /health threshold, killing in-flight work and never
# letting heavy self-improvement/research jobs complete.
LIVENESS_PATH = os.environ.get(
    "GATEWAY_LIVENESS_PATH",
    os.path.join(COMPOSE_PROJECT_DIR, "workspace", "healing", "gateway_liveness"),
)
HEARTBEAT_STALE = float(os.environ.get("HEARTBEAT_STALE_SECONDS", "60"))
# /health may legitimately be slow for the length of one heavy idle job
# (evolution cap 600 s; evolver docker-wait up to 1800 s). Only treat a LIVE
# process whose /health stays continuously down past this as a genuine loop
# wedge worth a restart — generous, so we never guillotine real work.
HEALTH_ESCALATE = float(os.environ.get("HEALTH_ESCALATE_SECONDS", "900"))
BUSY_LOG_INTERVAL = 120.0  # throttle the "busy, not restarting" log line

_session = requests.Session()


def log(msg: str) -> None:
    # launchd routes both stdout and stderr to LOG_PATH via the plist, so
    # printing once is enough — explicit file writes would duplicate every
    # entry. LOG_PATH is kept as an env var for documentation + future use
    # (e.g. an out-of-launchd run via `python -u gateway_watchdog.py`).
    print(f"[watchdog] {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


# ── Cooldown-state persistence (C3, 2026-05-18) ──────────────────────────
#
# Pre-fix: last_restart_at was a local variable in main(). A watchdog
# crash + launchd respawn (ThrottleInterval=30) reset the cooldown
# state, so the new watchdog could immediately restart the gateway on
# the next failure threshold — defeating the cooldown's anti-thrashing
# purpose. ThrottleInterval + grace + threshold combined gave a natural
# ~3 min backoff so the bug never bit in practice, but the cooldown's
# stated contract is now properly enforced across crashes.


def _load_restart_state() -> Optional[float]:
    """Restore last_restart_at from disk on watchdog startup.

    Returns None on first run or any failure — caller treats that as
    'no prior restart on record'. Stale state (older than the cooldown
    window) is treated as None too, since it can no longer gate
    anything. Never crashes the watchdog.
    """
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        ts = data.get("last_restart_at")
        if not isinstance(ts, (int, float)):
            return None
        if (time.time() - float(ts)) >= RESTART_COOLDOWN:
            # State is stale — cooldown already elapsed.
            return None
        return float(ts)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _save_restart_state(ts: float) -> None:
    """Persist last_restart_at to disk. Best-effort — a write failure
    just means the cooldown won't survive a watchdog crash, which is
    the pre-C3 behavior we still degrade to gracefully."""
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_restart_at": float(ts)}, f)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        log(f"Failed to persist restart state to {STATE_PATH}: {e}")


def probe_health() -> bool:
    try:
        resp = _session.get(HEALTH_URL, timeout=HEALTH_TIMEOUT)
        return 200 <= resp.status_code < 300
    except requests.exceptions.RequestException:
        return False
    except Exception as e:
        log(f"probe raised unexpected: {e}")
        return False


def read_liveness_age() -> Optional[float]:
    """Seconds since the gateway's process-liveness heartbeat was last written.

    The gateway (app/liveness.py) writes ``time.time()`` to LIVENESS_PATH from a
    daemon thread every few seconds. A fresh value means the PROCESS is alive and
    the interpreter is scheduling threads, EVEN IF the asyncio event loop is too
    busy to answer /health. Returns None if the file is missing/unreadable (e.g.
    a gateway build that predates the heartbeat) — callers treat None as "no
    liveness signal, fall back to the /health-threshold decision".
    """
    try:
        with open(LIVENESS_PATH, "r", encoding="utf-8") as f:
            ts = float(f.read().strip())
        return max(0.0, time.time() - ts)  # clamp clock-skew negatives to "fresh"
    except (OSError, ValueError):
        # Content unreadable/partial — fall back to mtime (atomic on close).
        try:
            return max(0.0, time.time() - os.path.getmtime(LIVENESS_PATH))
        except OSError:
            return None


def signal_alert(text: str) -> None:
    """Send a Signal alert via signal-cli JSON-RPC directly.

    Skips silently if no recipient is configured — alerts are nice-to-have,
    the recovery action is the load-bearing part.
    """
    if not SIGNAL_OWNER:
        return
    try:
        resp = requests.post(
            SIGNAL_CLI_URL.rstrip("/") + "/api/v1/rpc",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "send",
                "params": {"recipient": [SIGNAL_OWNER], "message": text},
            },
            timeout=10,
        )
        if resp.status_code != 200:
            log(f"signal alert HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        log(f"signal alert failed: {e}")


def gateway_diagnostics() -> str:
    """Inspect the gateway container to explain WHY it's unresponsive.

    Returns a short human-readable string for the restart alert + logs.
    A blank/unknown result is fine — diagnostics are best-effort.

    The key disambiguation:
      - OOMKilled=true / exit 137  → the 8 GB cgroup limit was hit; Docker
        SIGKILLed it and (restart: unless-stopped) is already restarting it.
        The fix is memory headroom, NOT the watchdog.
      - high RestartCount           → the container is in a Docker-level
        crash/restart loop independent of the watchdog.
      - Running=true, low uptime    → the gateway is simply still booting
        (lifespan startup hasn't finished, so uvicorn isn't serving yet).
    """
    try:
        fmt = (
            "{{.State.Running}}|{{.State.OOMKilled}}|{{.State.ExitCode}}|"
            "{{.RestartCount}}|{{.State.StartedAt}}"
        )
        result = subprocess.run(
            [DOCKER_BIN, "inspect",
             "--format", fmt,
             f"{os.path.basename(COMPOSE_PROJECT_DIR.rstrip('/'))}-{GATEWAY_SERVICE}-1"],
            cwd=COMPOSE_PROJECT_DIR, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            # Fall back to `docker compose ps` style name resolution failure —
            # just report what we got.
            return f"inspect rc={result.returncode}: {result.stderr.strip()[:120]}"
        running, oom, exit_code, restarts, started = (
            result.stdout.strip().split("|") + ["", "", "", "", ""]
        )[:5]
        parts = []
        if oom.lower() == "true":
            parts.append("⚠️ OOMKilled=true (hit the 8 GB memory limit)")
        if exit_code and exit_code not in ("0", ""):
            parts.append(f"last exit={exit_code}")
        if restarts and restarts not in ("0", ""):
            parts.append(f"docker RestartCount={restarts}")
        if running.lower() == "true" and started:
            parts.append(f"running, StartedAt={started}")
        elif running.lower() == "false":
            parts.append("container NOT running")
        return "; ".join(parts) if parts else "no anomaly in docker inspect"
    except Exception as e:  # noqa: BLE001 — diagnostics must never crash the watchdog
        return f"diagnostics unavailable: {e}"


def container_running() -> Optional[bool]:
    """Authoritative, GIL-independent liveness via docker inspect.

    Returns True if the gateway container is running and NOT OOMKilled, False if
    it is stopped/OOMKilled, None if the inspect itself failed (treat as
    'unknown' — never a restart trigger on its own).

    This is the death signal the in-process heartbeat CANNOT be: a pure-Python
    heartbeat thread starves alongside the event loop under a hard GIL hold (a
    C-extension that doesn't release the GIL — e.g. an embedding batch), so a
    stale heartbeat means "GIL busy", NOT necessarily "dead". docker inspect
    runs in the host kernel and is unaffected by the container's GIL.
    """
    try:
        result = subprocess.run(
            [DOCKER_BIN, "inspect", "--format",
             "{{.State.Running}}|{{.State.OOMKilled}}",
             f"{os.path.basename(COMPOSE_PROJECT_DIR.rstrip('/'))}-{GATEWAY_SERVICE}-1"],
            cwd=COMPOSE_PROJECT_DIR, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        running, oom = (result.stdout.strip().split("|") + ["", ""])[:2]
        if oom.lower() == "true":
            return False
        return running.lower() == "true"
    except Exception:  # noqa: BLE001 — never crash the watchdog on inspect
        return None


def restart_gateway() -> bool:
    log(f"Restarting compose service '{GATEWAY_SERVICE}' in {COMPOSE_PROJECT_DIR}")
    try:
        result = subprocess.run(
            [DOCKER_BIN, "compose", "restart", GATEWAY_SERVICE],
            cwd=COMPOSE_PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            log("Restart command returned cleanly")
            return True
        log(f"Restart failed (rc={result.returncode}): {result.stderr.strip()[:500]}")
        return False
    except subprocess.TimeoutExpired:
        log("Restart command timed out after 120s")
        return False
    except FileNotFoundError:
        log(f"docker binary not found at {DOCKER_BIN}; install or set DOCKER_BIN")
        return False
    except Exception as e:
        log(f"Restart raised: {e}")
        return False


def forwarder_loaded() -> Optional[bool]:
    """Whether the forwarder LaunchAgent is loaded in launchd.

    True if loaded, False if not, None if the check itself failed (treated as
    'unknown' — never triggers a bootstrap on its own). `launchctl list <label>`
    exits 0 when the agent is loaded and non-zero ("Could not find service …")
    when it isn't — the absence we saw on 2026-06-19.
    """
    try:
        result = subprocess.run(
            [LAUNCHCTL_BIN, "list", FORWARDER_LABEL],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception as e:  # noqa: BLE001 — guard must never crash the watchdog
        log(f"forwarder liveness check raised: {e}")
        return None


def bootstrap_forwarder() -> bool:
    """Re-bootstrap the forwarder LaunchAgent into the user's GUI domain.

    Idempotent: an already-loaded race ('service already loaded', rc 5/EALREADY)
    is treated as success. Returns False on a real failure (plist missing,
    launchctl error) so the caller can alert that manual intervention is needed.
    """
    uid = os.getuid()
    try:
        result = subprocess.run(
            [LAUNCHCTL_BIN, "bootstrap", f"gui/{uid}", FORWARDER_PLIST],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return True
        combined = (result.stderr + result.stdout).lower()
        if "already" in combined:  # already-loaded race — benign
            return True
        log(f"forwarder bootstrap failed (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:200]}")
        return False
    except FileNotFoundError:
        log(f"{LAUNCHCTL_BIN} not found; cannot recover forwarder")
        return False
    except Exception as e:  # noqa: BLE001 — never crash the watchdog
        log(f"forwarder bootstrap raised: {e}")
        return False


def maybe_recover_forwarder(last_action_at: float) -> float:
    """Ensure the forwarder LaunchAgent is loaded; re-bootstrap if not.

    Cheap to call every poll: it's a `launchctl list` and, only when the agent
    is genuinely unloaded, one throttled bootstrap. Returns the (possibly
    updated) timestamp of the last bootstrap attempt for cooldown tracking.
    """
    if not FORWARDER_GUARD_ENABLED:
        return last_action_at
    loaded = forwarder_loaded()
    if loaded is None or loaded:
        # Unknown (check failed) or healthy → nothing to do. KeepAlive owns the
        # crash-restart case while it's loaded.
        return last_action_at
    now = time.time()
    if (now - last_action_at) < FORWARDER_BOOTSTRAP_COOLDOWN:
        # Acted recently; don't thrash launchctl or spam alerts on a flapping agent.
        return last_action_at
    log(f"Forwarder '{FORWARDER_LABEL}' is NOT loaded — inbound Signal is dead; "
        f"re-bootstrapping from {FORWARDER_PLIST}")
    ok = bootstrap_forwarder()
    if ok:
        log("Forwarder re-bootstrapped; inbound should resume (signal-cli backlog drains)")
        signal_alert(
            f"♻️ Signal forwarder was DOWN (LaunchAgent '{FORWARDER_LABEL}' "
            f"unloaded) — watchdog re-bootstrapped it. Inbound messages flowing "
            f"again; any backlog held by signal-cli will drain."
        )
    else:
        signal_alert(
            f"❌ Signal forwarder '{FORWARDER_LABEL}' is DOWN and the watchdog "
            f"could NOT re-bootstrap it. Inbound Signal is dead — manual fix:\n"
            f"launchctl bootstrap gui/{os.getuid()} {FORWARDER_PLIST}"
        )
    return now


def maybe_recover_keeper(last_action_at: float) -> float:
    """Ensure the agent-keeper LaunchAgent is loaded; re-bootstrap if not.

    The keeper (StartInterval) is what keeps THIS watchdog — and every other
    botarmy agent — loaded; guarding it here makes the two mutually protective,
    so no single `launchctl bootout` can leave both recovery agents dead. Cheap
    (a launchctl list); only acts when the keeper is genuinely unloaded.
    """
    if not KEEPER_GUARD_ENABLED:
        return last_action_at
    try:
        loaded = subprocess.run(
            [LAUNCHCTL_BIN, "list", KEEPER_LABEL],
            capture_output=True, text=True, timeout=10,
        ).returncode == 0
    except Exception as e:  # noqa: BLE001 — guard must never crash the watchdog
        log(f"keeper liveness check raised: {e}")
        return last_action_at
    if loaded:
        return last_action_at
    now = time.time()
    if (now - last_action_at) < FORWARDER_BOOTSTRAP_COOLDOWN:
        return last_action_at  # acted recently; don't thrash/spam on a flapping agent
    log(f"Agent-keeper '{KEEPER_LABEL}' is NOT loaded — re-bootstrapping from {KEEPER_PLIST}")
    uid = os.getuid()
    try:
        r = subprocess.run(
            [LAUNCHCTL_BIN, "bootstrap", f"gui/{uid}", KEEPER_PLIST],
            capture_output=True, text=True, timeout=30,
        )
        ok = r.returncode == 0 or "already" in (r.stderr + r.stdout).lower()
        if not ok:
            log(f"keeper bootstrap failed (rc={r.returncode}): "
                f"{(r.stderr or r.stdout).strip()[:200]}")
    except Exception as e:  # noqa: BLE001 — never crash the watchdog
        log(f"keeper bootstrap raised: {e}")
        ok = False
    if ok:
        log(f"Re-bootstrapped '{KEEPER_LABEL}'")
        signal_alert(
            f"♻️ agent-keeper was DOWN (LaunchAgent '{KEEPER_LABEL}' unloaded) — "
            f"watchdog re-bootstrapped it. Host-agent recovery layer restored."
        )
    else:
        signal_alert(
            f"❌ agent-keeper '{KEEPER_LABEL}' is DOWN and the watchdog could NOT "
            f"re-bootstrap it — manual: launchctl bootstrap gui/{uid} {KEEPER_PLIST}"
        )
    return now


def main() -> int:
    log(f"Starting — poll {HEALTH_URL} every {POLL_INTERVAL:.0f}s "
        f"(timeout {HEALTH_TIMEOUT:.0f}s), restart after {FAILURE_THRESHOLD} consecutive failures, "
        f"cooldown {RESTART_COOLDOWN:.0f}s, grace {RESTART_GRACE:.0f}s")
    log(f"Liveness gate: heartbeat {LIVENESS_PATH} (stale>{HEARTBEAT_STALE:.0f}s ⇒ restart); "
        f"a LIVE process with slow /health is NOT restarted until /health is down "
        f">{HEALTH_ESCALATE:.0f}s")
    if not SIGNAL_OWNER:
        log("SIGNAL_OWNER_NUMBER not set; alerts disabled (recovery still runs)")
    if FORWARDER_GUARD_ENABLED:
        log(f"Forwarder guard ON: ensuring '{FORWARDER_LABEL}' stays loaded "
            f"(plist {FORWARDER_PLIST}, bootstrap cooldown {FORWARDER_BOOTSTRAP_COOLDOWN:.0f}s)")
    if KEEPER_GUARD_ENABLED:
        log(f"Keeper guard ON: ensuring '{KEEPER_LABEL}' stays loaded "
            f"(mutual — the keeper keeps this watchdog loaded in return)")

    consecutive_failures = 0
    last_forwarder_action_at = 0.0  # last forwarder bootstrap attempt (cooldown gate)
    last_keeper_action_at = 0.0     # last keeper bootstrap attempt (cooldown gate)
    health_down_since: Optional[float] = None  # when /health started failing (this outage)
    last_busy_log = 0.0  # throttle the "busy, not restarting" log line
    last_restart_at: Optional[float] = _load_restart_state()
    if last_restart_at is not None:
        remaining = int(RESTART_COOLDOWN - (time.time() - last_restart_at))
        log(
            f"Restored prior restart state — cooldown {remaining}s remaining "
            f"(state file: {STATE_PATH})"
        )
    # Initial boot grace: the gateway may be mid-boot when this watchdog
    # (re)starts (launchd relaunch, host reboot, fresh install). /health does
    # not answer until the gateway's lifespan startup completes, so probing
    # immediately would mis-read a normal cold boot as a hang and restart it —
    # the exact loop that floods the operator with alerts. Skip probes for
    # BOOT_GRACE on watchdog startup.
    grace_until: float = time.time() + BOOT_GRACE
    log(f"Initial boot grace: skipping probes for {BOOT_GRACE:.0f}s "
        f"(gateway may be booting)")

    while True:
        now = time.time()
        # Forwarder guard runs every iteration, independent of gateway health and
        # boot grace — the forwarder being unloaded has nothing to do with the
        # gateway booting. Cheap (a launchctl list); only acts when unloaded.
        last_forwarder_action_at = maybe_recover_forwarder(last_forwarder_action_at)
        last_keeper_action_at = maybe_recover_keeper(last_keeper_action_at)
        if now < grace_until:
            # Inside post-restart grace; don't even probe yet.
            time.sleep(min(POLL_INTERVAL, grace_until - now))
            continue

        ok = probe_health()
        if ok:
            if consecutive_failures:
                log(f"Recovered after {consecutive_failures} failed probe(s)")
                consecutive_failures = 0
                health_down_since = None
                last_busy_log = 0.0
        else:
            consecutive_failures += 1
            if consecutive_failures == 1:
                health_down_since = now
                log("First failed probe — watching")
            elif consecutive_failures % max(1, FAILURE_THRESHOLD // 2) == 0:
                log(f"Failed probe {consecutive_failures}/{FAILURE_THRESHOLD}")

            if consecutive_failures >= FAILURE_THRESHOLD:
                in_cooldown = (
                    last_restart_at is not None
                    and (now - last_restart_at) < RESTART_COOLDOWN
                )
                # ── Liveness gate (2026-06-01) ─────────────────────────
                # Decide busy-but-alive vs dead/wedged using signals that do NOT
                # starve with the event loop:
                #   • death  → docker inspect (container_running): OS-level,
                #     GIL-independent. The in-process heartbeat can't be this —
                #     it starves alongside the loop under a hard GIL hold.
                #   • wedge  → duration (/health down past HEALTH_ESCALATE):
                #     longer than any legitimate heavy job (evolution cap 600 s),
                #     so a continuous outage past it is a real wedge, not work.
                # The heartbeat age is kept only as a human-readable log hint.
                age = read_liveness_age()
                hb = f"{age:.0f}s" if age is not None else "n/a"
                running = container_running()
                down_for = (
                    int(now - health_down_since) if health_down_since
                    else int(consecutive_failures * POLL_INTERVAL)
                )
                should_restart = False
                cause = ""
                if in_cooldown:
                    remaining = int(RESTART_COOLDOWN - (now - last_restart_at))
                    log(f"Threshold breached but cooldown active ({remaining}s remaining)")
                elif running is False:
                    should_restart = True
                    cause = "container stopped/OOMKilled"
                elif down_for >= HEALTH_ESCALATE:
                    should_restart = True
                    cause = (f"/health down >{int(HEALTH_ESCALATE)}s with container "
                             f"up — event loop wedged")
                else:
                    # Container up (or inspect unknown) and /health has been down
                    # less than any legitimate heavy job could hold the loop. Do
                    # NOT restart — let the work finish; /health recovers on its
                    # own. This is the case the old watchdog restart-looped on.
                    if (now - last_busy_log) >= BUSY_LOG_INTERVAL:
                        log(f"/health down ~{down_for}s but container UP "
                            f"(heartbeat {hb}) — busy/working, NOT restarting "
                            f"(escalate at {int(HEALTH_ESCALATE)}s)")
                        last_busy_log = now

                if should_restart:
                    diag = gateway_diagnostics()
                    log(f"Threshold breached — {cause}; /health down ~{down_for}s "
                        f"(heartbeat {hb}); diagnostics: {diag}; restarting")
                    signal_alert(
                        f"⚠️ Gateway watchdog: restarting {GATEWAY_SERVICE}.\n"
                        f"Reason: {cause}\n"
                        f"/health down ~{down_for}s. {diag}"
                    )
                    success = restart_gateway()
                    last_restart_at = time.time()
                    _save_restart_state(last_restart_at)
                    grace_until = last_restart_at + RESTART_GRACE
                    consecutive_failures = 0
                    health_down_since = None
                    last_busy_log = 0.0
                    if success:
                        signal_alert(
                            f"♻️ Gateway restart issued. Probing again in {int(RESTART_GRACE)}s."
                        )
                    else:
                        signal_alert(
                            "❌ Gateway restart FAILED — manual intervention needed."
                        )

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Interrupted; exiting")
        sys.exit(0)
