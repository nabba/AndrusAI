"""
Host-side git-pull deploy poller for the BotArmy gateway.

Pull-based auto-deploy: a launchd LaunchAgent runs this on a ``StartInterval``
(default ~180 s). On each tick it ``git fetch``es the remote and, when
``origin/<branch>`` is strictly ahead of the local checkout, runs
``scripts/deploy_gateway.sh`` — so a merged PR redeploys the gateway with NO
inbound port, NO public exposure (no Tailscale Funnel), and NO GitHub-side
webhook configuration. The host reaches out; nothing reaches in.

Why a poller and not the #133 webhook (scripts/deploy_webhook.py): the deploy
is a host docker build, so it has to run on the host either way. A poller
avoids exposing an internet-reachable code-execution endpoint, and needs no
out-of-band Funnel + GitHub setup — the exact friction that left the webhook
uninstalled. This mirrors the other host LaunchAgents (warm-spare sync,
db-backup, browse-collector — all pull/timer-based) and the gateway watchdog
(host-side, signal-cli alerts).

Stdlib-only (urllib for the optional Signal alert) so it runs on the system
``/usr/bin/python3`` and isn't coupled to the gateway venv it might be
redeploying.

SAFETY
  - Single-flight: a non-blocking ``fcntl.flock`` means an in-progress deploy
    is never stacked behind the next tick. A second tick exits immediately.
  - Branch-scoped: acts only when the local HEAD is on ``<branch>``. If the
    operator has checked out a feature branch, the poller stays out of the way.
  - Fast-forward-only: deploys only when local HEAD is an ANCESTOR of
    ``origin/<branch>`` (a clean fast-forward). A diverged or locally-ahead
    tree is left untouched and surfaced once — never clobbered.
  - The actual ``git pull --ff-only`` + rebuild lives in deploy_gateway.sh;
    this poller only decides *whether* to invoke it. One source of truth for
    what a deploy does.
  - A failed build does not retry-loop: deploy_gateway.sh's pull moves local
    HEAD to the remote first, so the next tick reads "up to date". The operator
    is alerted and fixes forward (a new commit) or reruns the deploy by hand.
  - Collection gate (this repo's CI substitute while GitHub Actions is
    billing-locked): the about-to-deploy SHA is checked out into a throwaway
    worktree and run through `pytest --collect-only`; a collection error
    WITHHOLDS the deploy (the container keeps its last-good build) and alerts
    once per bad SHA. Fail-OPEN on gate-infra trouble (missing venv, timeout) so
    a broken gate can never wedge every deploy. See collection_gate().

Environment:
    DEPLOY_POLLER_BRANCH       branch to track (default main)
    DEPLOY_POLLER_REMOTE       remote name (default origin)
    DEPLOY_POLLER_REPO_ROOT    repo to operate on (default <script>/..)
    DEPLOY_SCRIPT              deploy script (default <repo>/scripts/deploy_gateway.sh)
    GIT_BIN                    git binary (default: git on PATH)
    DEPLOY_POLLER_LOG          log file (default <repo>/workspace/healing/.deploy_poller.log)
    DEPLOY_POLLER_LOCK         lock file (default ~/.crewai-bridge/deploy_poller.lock)
    DEPLOY_POLLER_STATE        state file (default ~/.crewai-bridge/deploy_poller_state.json)
    DEPLOY_TIMEOUT_SECONDS     hard cap on deploy_gateway.sh (default 1800)
    DEPLOY_POLLER_GATE_ENABLED collection gate on/off (default 1; 0 disables)
    DEPLOY_POLLER_GATE_CMD     gate command (default: <repo>/.venv pytest --collect-only)
    DEPLOY_POLLER_GATE_TIMEOUT hard cap on the gate run, seconds (default 600)
    DEPLOY_POLLER_GATE_STATE   gate dedup state (default ~/.crewai-bridge/deploy_poller_gate.json)
    SIGNAL_CLI_HTTP_URL        signal-cli JSON-RPC endpoint (default http://127.0.0.1:7583)
    SIGNAL_OWNER_NUMBER        alert recipient (alerts disabled if unset)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

_REPO_ROOT_DEFAULT = Path(__file__).resolve().parent.parent

# ── Status codes returned by check_once() ───────────────────────────────────
# Routine no-ops we never log (they recur every tick): UP_TO_DATE, WRONG_BRANCH.
# Everything else is either a deploy event or a problem worth surfacing once.
UP_TO_DATE = "uptodate"
WRONG_BRANCH = "wrong_branch"
DIVERGED = "diverged"
FETCH_FAILED = "fetch_failed"
RESOLVE_FAILED = "resolve_failed"
NOT_GIT = "not_git"
DEPLOYED = "deployed"
DEPLOY_FAILED = "deploy_failed"
GATE_BLOCKED = "gate_blocked"                  # collection gate found errors → deploy withheld
GATE_ALREADY_BLOCKED = "gate_already_blocked"  # same bad SHA as a prior tick → stay silent

_SILENT_CODES = frozenset({UP_TO_DATE, WRONG_BRANCH, GATE_ALREADY_BLOCKED})
_DEPLOY_CODES = frozenset({DEPLOYED, DEPLOY_FAILED})

_LOG_PATH: Optional[str] = None  # set by main(); None ⇒ print-only (tests)


def log(msg: str) -> None:
    line = f"[deploy-poller] {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line, flush=True)
    if _LOG_PATH:
        try:
            Path(_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # logging is best-effort; never crash the poller


# ── Pure decision ───────────────────────────────────────────────────────────
def decide(
    current_branch: str,
    target_branch: str,
    local_sha: str,
    remote_sha: str,
    local_is_ancestor_of_remote: bool,
) -> Tuple[str, str]:
    """Decide whether to deploy. Pure — no git, no I/O. Returns (code, detail).

    Deploy only on a clean fast-forward of the tracked branch:
      - not on the tracked branch        → WRONG_BRANCH (operator's working tree)
      - local == remote                  → UP_TO_DATE
      - local is an ancestor of remote   → DEPLOYED (remote strictly ahead)
      - otherwise (diverged / local ahead) → DIVERGED (leave it alone)
    """
    if current_branch != target_branch:
        return WRONG_BRANCH, f"on branch {current_branch!r}, not {target_branch!r}"
    if local_sha == remote_sha:
        return UP_TO_DATE, "up to date"
    if local_is_ancestor_of_remote:
        return DEPLOYED, f"{target_branch} {local_sha[:8]} → {remote_sha[:8]} (fast-forward)"
    return DIVERGED, (
        f"local {target_branch!r} ({local_sha[:8]}) has diverged from "
        f"remote ({remote_sha[:8]}) — not fast-forwardable; left for manual review"
    )


# ── Git helpers ───────────────────────────────────────────────────────────--
def _git(git_bin: str, repo_root: str, *args: str, timeout: float = 60.0) -> Optional[str]:
    """Run a git command in repo_root. Returns stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            [git_bin, *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"git {' '.join(args)} raised: {e}")
        return None
    if result.returncode != 0:
        log(f"git {' '.join(args)} failed (rc={result.returncode}): "
            f"{result.stderr.strip()[:200]}")
        return None
    return result.stdout.strip()


def _is_ancestor(git_bin: str, repo_root: str, ancestor_ref: str, descendant_ref: str) -> bool:
    """True iff ancestor_ref is an ancestor of descendant_ref (exit 0)."""
    try:
        result = subprocess.run(
            [git_bin, "merge-base", "--is-ancestor", ancestor_ref, descendant_ref],
            cwd=repo_root, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


# ── Collection gate ────────────────────────────────────────────────────────--
def collection_gate(
    *,
    repo_root: str,
    remote_sha: str,
    git_bin: str,
    gate_cmd: str,
    gate_timeout: float,
) -> Tuple[bool, str]:
    """Run the test-collection gate against the about-to-deploy code.

    Checks out ``remote_sha`` into a THROWAWAY worktree (the live checkout is
    never moved — ``deploy_gateway.sh`` stays the sole pull authority) and runs
    ``gate_cmd`` (default ``pytest --collect-only``) there. Returns (ok, detail).

    Posture — fail-OPEN on gate-infra trouble, fail-CLOSED only on a real finding,
    because a broken GATE that wedged every deploy would be strictly worse than the
    pre-gate behaviour (no gate at all):
      - rc 0                                   → clean; deploy proceeds.
      - rc 2 + a pytest "collected … error"    → BLOCK (the real signal).
        summary
      - worktree add fails / pytest missing /  → fail-OPEN (deploy proceeds, logged).
        timeout / rc 5 (no tests) / anything
        ambiguous
    An empty ``gate_cmd`` disables the gate entirely (returns ok).
    """
    if not gate_cmd.strip():
        return True, "gate disabled"
    import shutil
    import tempfile

    wt = tempfile.mkdtemp(prefix="deploy-gate-")
    try:
        if _git(git_bin, repo_root, "worktree", "add", "--detach", "--force", wt, remote_sha) is None:
            return True, "gate setup failed — worktree add (fail-open)"
        try:
            result = subprocess.run(
                gate_cmd, cwd=wt, capture_output=True, text=True,
                timeout=gate_timeout, shell=True,
            )
        except subprocess.TimeoutExpired:
            return True, f"gate timed out after {int(gate_timeout)}s (fail-open)"
        except (OSError, subprocess.SubprocessError) as e:
            return True, f"gate runner error: {e} (fail-open)"
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        if result.returncode == 0:
            return True, "collection clean"
        low = out.lower()
        if result.returncode == 2 and "collected" in low and "error" in low:
            errs = [ln for ln in out.splitlines() if ln.startswith("ERROR ")]
            summary = (errs[0] if errs else out.splitlines()[-1] if out else "")[:160]
            return False, f"collection errors — {summary}"
        # pytest could not run cleanly (no venv / pytest missing / rc 5 no-tests …)
        return True, f"gate could not run (rc={result.returncode}, fail-open)"
    finally:
        _git(git_bin, repo_root, "worktree", "remove", "--force", wt)
        shutil.rmtree(wt, ignore_errors=True)


# ── Deploy + alert ───────────────────────────────────────────────────────────
def run_deploy(deploy_script: str, repo_root: str, timeout: float) -> bool:
    """Run the deploy script once, mirroring its output into the log. Returns ok."""
    if not Path(deploy_script).exists():
        log(f"DEPLOY ERROR — deploy script not found: {deploy_script}")
        return False
    log(f"DEPLOY START — {deploy_script}")
    try:
        result = subprocess.run(
            ["/bin/bash", deploy_script],
            cwd=repo_root, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log(f"DEPLOY TIMEOUT — exceeded {int(timeout)}s; check docker manually")
        return False
    except Exception as e:  # noqa: BLE001 — never let a deploy crash the poller
        log(f"DEPLOY ERROR — {e}")
        return False
    out = (result.stdout or "") + (result.stderr or "")
    # Keep the tail; deploy_gateway.sh already prints its own progress markers.
    tail = out.strip().splitlines()[-40:]
    for ln in tail:
        log(f"  | {ln}")
    log(f"DEPLOY END — exit {result.returncode}")
    return result.returncode == 0


def signal_alert(text: str) -> None:
    """Best-effort Signal alert via signal-cli JSON-RPC (mirrors gateway_watchdog.py).

    No-op when SIGNAL_OWNER_NUMBER is unset — alerts are nice-to-have; the
    deploy is the load-bearing part. Uses urllib so the poller stays stdlib-only.
    """
    owner = os.environ.get("SIGNAL_OWNER_NUMBER", "")
    if not owner:
        return
    url = os.environ.get("SIGNAL_CLI_HTTP_URL", "http://127.0.0.1:7583").rstrip("/") + "/api/v1/rpc"
    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "send",
        "params": {"recipient": [owner], "message": text},
    }).encode()
    try:
        import urllib.request
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — loopback only
            if resp.status != 200:
                log(f"signal alert HTTP {resp.status}")
    except Exception as e:  # noqa: BLE001 — alerting must never crash the poller
        log(f"signal alert failed: {e}")


# ── One poll ─────────────────────────────────────────────────────────────────
def check_once(
    *,
    repo_root: str,
    target_branch: str,
    remote: str,
    git_bin: str,
    deploy_script: str,
    deploy_timeout: float,
    alert: Optional[Callable[[str], None]] = None,
    gate_cmd: str = "",
    gate_timeout: float = 600.0,
    gate_state_path: str = "",
    gate_fn: Optional[Callable[..., Tuple[bool, str]]] = None,
) -> Tuple[str, str]:
    """Do one poll: fetch, decide, gate, deploy if a clean fast-forward is available.

    Returns (code, detail). Never raises. `alert` (if given) is called with a
    human-readable message at deploy start and on the deploy result.

    When `gate_cmd` is non-empty, the about-to-deploy SHA must pass the collection
    gate (`gate_fn`, default `collection_gate`) before `run_deploy` is invoked. A
    blocked SHA is recorded in `gate_state_path` so the alert fires once, not every
    tick — subsequent ticks on the same bad SHA return GATE_ALREADY_BLOCKED (silent)
    until a fix commit advances the branch. `gate_cmd=""` ⇒ gate disabled (prior
    behaviour, for tests + opt-out).
    """
    current = _git(git_bin, repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if current is None:
        return NOT_GIT, "not a git repo / git unavailable"
    if current != target_branch:
        return WRONG_BRANCH, f"on branch {current!r}, not {target_branch!r}"

    # Update the remote-tracking ref only; the pull happens inside deploy_gateway.sh.
    if _git(git_bin, repo_root, "fetch", "--quiet", remote, target_branch) is None:
        return FETCH_FAILED, f"git fetch {remote} {target_branch} failed"

    local_sha = _git(git_bin, repo_root, "rev-parse", "HEAD")
    remote_sha = _git(git_bin, repo_root, "rev-parse", f"{remote}/{target_branch}")
    if not local_sha or not remote_sha:
        return RESOLVE_FAILED, "could not resolve local/remote SHAs"

    is_anc = (
        local_sha != remote_sha
        and _is_ancestor(git_bin, repo_root, local_sha, f"{remote}/{target_branch}")
    )
    code, detail = decide(current, target_branch, local_sha, remote_sha, is_anc)
    if code != DEPLOYED:
        return code, detail

    # ── Collection gate: never deploy code that fails `pytest --collect-only`. ──
    if gate_cmd.strip():
        if remote_sha == _load_blocked_sha(gate_state_path):
            # Same bad SHA we already blocked + alerted on; stay silent (the
            # container keeps its last-good build) until a fix advances the branch.
            return GATE_ALREADY_BLOCKED, f"{remote_sha[:8]} previously failed the collection gate"
        _gate = gate_fn or collection_gate
        gate_ok, gate_detail = _gate(
            repo_root=repo_root, remote_sha=remote_sha, git_bin=git_bin,
            gate_cmd=gate_cmd, gate_timeout=gate_timeout,
        )
        if not gate_ok:
            _save_blocked_sha(gate_state_path, remote_sha)
            log(f"DEPLOY WITHHELD — {remote_sha[:8]} failed the collection gate: {gate_detail}")
            if alert:
                alert(
                    f"⛔ Deploy withheld at {remote_sha[:8]} — `pytest --collect-only` FAILED "
                    f"({gate_detail}). Gateway stays on the last-good build; push a fix commit."
                )
            return GATE_BLOCKED, gate_detail

    log(f"{remote}/{target_branch} advanced — {detail}; deploying")
    if alert:
        alert(f"🚀 Auto-deploy: {remote}/{target_branch} → {remote_sha[:8]} — rebuilding gateway.")
    ok = run_deploy(deploy_script, repo_root, deploy_timeout)
    if gate_cmd.strip():
        _save_blocked_sha(gate_state_path, "")  # this SHA deployed (or tried) — clear any stale block
    if alert:
        if ok:
            alert(f"✅ Auto-deploy done: gateway rebuilt at {remote_sha[:8]}.")
        else:
            alert(
                f"❌ Auto-deploy FAILED at {remote_sha[:8]} — see .deploy_poller.log. "
                f"Local main already advanced; rerun scripts/deploy_gateway.sh once fixed."
            )
    return (DEPLOYED, detail) if ok else (DEPLOY_FAILED, detail)


# ── State (transition-only logging for problem codes) ────────────────────────
def _load_last_code(state_path: str) -> Optional[str]:
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f).get("last_code")
    except (OSError, ValueError):
        return None


def _save_last_code(state_path: str, code: str) -> None:
    try:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_code": code, "at": time.time()}, f)
        os.replace(tmp, state_path)
    except OSError:
        pass


# ── Collection-gate dedup state (the SHA last withheld by the gate) ──────────--
def _load_blocked_sha(state_path: str) -> str:
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f).get("blocked_sha", "") or ""
    except (OSError, ValueError):
        return ""


def _save_blocked_sha(state_path: str, sha: str) -> None:
    if not state_path:
        return
    try:
        Path(state_path).parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"blocked_sha": sha, "at": time.time()}, f)
        os.replace(tmp, state_path)
    except OSError:
        pass


def main() -> int:
    global _LOG_PATH

    repo_root = os.environ.get("DEPLOY_POLLER_REPO_ROOT", str(_REPO_ROOT_DEFAULT))
    target_branch = os.environ.get("DEPLOY_POLLER_BRANCH", "main")
    remote = os.environ.get("DEPLOY_POLLER_REMOTE", "origin")
    git_bin = os.environ.get("GIT_BIN", "git")
    deploy_script = os.environ.get(
        "DEPLOY_SCRIPT", str(Path(repo_root) / "scripts" / "deploy_gateway.sh"))
    deploy_timeout = float(os.environ.get("DEPLOY_TIMEOUT_SECONDS", "1800"))
    _LOG_PATH = os.environ.get(
        "DEPLOY_POLLER_LOG", str(Path(repo_root) / "workspace" / "healing" / ".deploy_poller.log"))
    lock_path = os.environ.get(
        "DEPLOY_POLLER_LOCK", os.path.expanduser("~/.crewai-bridge/deploy_poller.lock"))
    state_path = os.environ.get(
        "DEPLOY_POLLER_STATE", os.path.expanduser("~/.crewai-bridge/deploy_poller_state.json"))

    # ── Collection gate: `pytest --collect-only` against the about-to-deploy SHA
    #    in a throwaway worktree; a collection error withholds the deploy. Runs in
    #    the gateway venv (heavy deps); fail-OPEN if that venv is absent. Disable
    #    with DEPLOY_POLLER_GATE_ENABLED=0. ────────────────────────────────────--
    gate_enabled = os.environ.get("DEPLOY_POLLER_GATE_ENABLED", "1") not in ("0", "false", "False", "")
    _venv_py = Path(repo_root) / ".venv" / "bin" / "python"
    default_gate_cmd = (
        f'"{_venv_py}" -m pytest tests/ --collect-only -q -p no:cacheprovider'
    )
    gate_cmd = os.environ.get("DEPLOY_POLLER_GATE_CMD", default_gate_cmd) if gate_enabled else ""
    gate_timeout = float(os.environ.get("DEPLOY_POLLER_GATE_TIMEOUT", "600"))
    gate_state_path = os.environ.get(
        "DEPLOY_POLLER_GATE_STATE", os.path.expanduser("~/.crewai-bridge/deploy_poller_gate.json"))

    # ── Single-flight: a deploy from a prior tick may still be building. ──────
    import fcntl
    try:
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        lock_fh = open(lock_path, "w", encoding="utf-8")
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Another poll/deploy holds the lock — exit quietly, next tick retries.
        return 0

    try:
        code, detail = check_once(
            repo_root=repo_root, target_branch=target_branch, remote=remote,
            git_bin=git_bin, deploy_script=deploy_script,
            deploy_timeout=deploy_timeout, alert=signal_alert,
            gate_cmd=gate_cmd, gate_timeout=gate_timeout, gate_state_path=gate_state_path,
        )
        # Logging policy: deploy events already logged inside check_once/run_deploy;
        # routine no-ops stay silent; a problem state is surfaced once per transition.
        if code not in _DEPLOY_CODES and code not in _SILENT_CODES:
            if code != _load_last_code(state_path):
                log(detail)
        _save_last_code(state_path, code)
        return 0
    finally:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()
        except OSError:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
