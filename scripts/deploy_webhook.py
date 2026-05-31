"""
Host-side deploy webhook for the BotArmy gateway.

Listens for a GitHub webhook and, on a merge to the deploy branch (default
``main``), runs ``scripts/deploy_gateway.sh`` — so a merged PR redeploys the
gateway with no terminal. Runs ON THE MAC (the deploy is a host docker build);
it cannot run in a CI sandbox or inside the container.

SECURITY MODEL (this endpoint runs code on the host — treat it as such):
  - Every request MUST carry a valid ``X-Hub-Signature-256`` HMAC over the raw
    body, keyed by ``DEPLOY_WEBHOOK_SECRET`` (the same secret you paste into the
    GitHub webhook config). Missing/invalid signature → 401, no action. The
    secret is mandatory; the server refuses to start without it.
  - Only a push to / a merged PR into the configured branch triggers a deploy.
    Pushes to other branches, unmerged PR closes, and every other event are
    acknowledged and ignored.
  - Optional ``DEPLOY_REPO`` (e.g. ``nabba/andrusai``) pins the repo; payloads
    from any other repo are ignored.
  - Single-flight: a deploy already in progress returns 409 rather than
    stacking a second concurrent ``docker compose build``.
  - Binds 127.0.0.1 by default. To receive GitHub's webhook you expose it via
    Tailscale Funnel (see docs/SIGNAL_RESILIENCE.md) — do NOT bind 0.0.0.0 on
    an untrusted network. HMAC is the real boundary regardless of bind.

Environment:
    DEPLOY_WEBHOOK_SECRET   shared HMAC secret (REQUIRED — same as GitHub config)
    DEPLOY_WEBHOOK_BIND     interface to bind (default 127.0.0.1)
    DEPLOY_WEBHOOK_PORT     port to listen on (default 9200)
    DEPLOY_WEBHOOK_BRANCH   branch whose merges trigger deploy (default main)
    DEPLOY_REPO             optional "owner/name" allow-pin (default: any)
    DEPLOY_SCRIPT           deploy script path (default <repo>/scripts/deploy_gateway.sh)
    DEPLOY_WEBHOOK_LOG      log file (default <repo>/workspace/healing/.deploy_webhook.log)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

def _resolve_secret() -> str:
    """Secret from env, else from a gitignored host file (never committed).

    The install script generates ``~/.crewai-bridge/deploy_webhook_secret`` so
    the launchd plist can stay secret-free in git. Env wins if both are set.
    """
    env_secret = os.environ.get("DEPLOY_WEBHOOK_SECRET", "")
    if env_secret:
        return env_secret
    secret_file = os.environ.get(
        "DEPLOY_WEBHOOK_SECRET_FILE",
        os.path.expanduser("~/.crewai-bridge/deploy_webhook_secret"),
    )
    try:
        return Path(secret_file).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


SECRET = _resolve_secret()
BIND = os.environ.get("DEPLOY_WEBHOOK_BIND", "127.0.0.1")
PORT = int(os.environ.get("DEPLOY_WEBHOOK_PORT", "9200"))
BRANCH = os.environ.get("DEPLOY_WEBHOOK_BRANCH", "main")
REPO_PIN = os.environ.get("DEPLOY_REPO", "")
DEPLOY_SCRIPT = os.environ.get(
    "DEPLOY_SCRIPT", str(_REPO_ROOT / "scripts" / "deploy_gateway.sh")
)
LOG_PATH = os.environ.get(
    "DEPLOY_WEBHOOK_LOG",
    str(_REPO_ROOT / "workspace" / "healing" / ".deploy_webhook.log"),
)

_MAX_BODY = 5 * 1024 * 1024  # GitHub payloads are small; cap to refuse abuse.

# Single-flight: never run two deploys at once.
_deploy_lock = threading.Lock()
_deploying = threading.Event()


def log(msg: str) -> None:
    line = f"[deploy-webhook] {time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}"
    print(line, flush=True)
    try:
        Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # logging is best-effort; never crash the listener


def _signature_ok(raw: bytes, header: str | None) -> bool:
    """Constant-time verify GitHub's X-Hub-Signature-256 over the raw body."""
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _should_deploy(event: str, payload: dict) -> tuple[bool, str]:
    """Decide whether this event is a merge to the deploy branch.

    Returns (deploy?, human-readable reason).
    """
    repo = (payload.get("repository") or {}).get("full_name", "")
    if REPO_PIN and repo and repo.lower() != REPO_PIN.lower():
        return False, f"ignored: repo {repo!r} != pinned {REPO_PIN!r}"

    if event == "ping":
        return False, "ping"

    if event == "push":
        ref = payload.get("ref", "")
        if ref == f"refs/heads/{BRANCH}":
            return True, f"push to {BRANCH}"
        return False, f"ignored: push to {ref!r} (want refs/heads/{BRANCH})"

    if event == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request") or {}
        base = (pr.get("base") or {}).get("ref", "")
        if action == "closed" and pr.get("merged") is True and base == BRANCH:
            return True, f"PR #{payload.get('number')} merged into {BRANCH}"
        return False, f"ignored: pull_request action={action!r} merged={pr.get('merged')} base={base!r}"

    return False, f"ignored: event {event!r}"


def _run_deploy(reason: str) -> None:
    """Run the deploy script once, serialized, logging output + exit code."""
    if not _deploy_lock.acquire(blocking=False):
        log(f"deploy requested ({reason}) but one is already running — skipped")
        return
    _deploying.set()
    try:
        log(f"DEPLOY START — {reason}")
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as logf:
                proc = subprocess.run(
                    ["/bin/bash", DEPLOY_SCRIPT],
                    cwd=str(_REPO_ROOT),
                    stdout=logf, stderr=subprocess.STDOUT,
                    timeout=1800,  # 30 min hard cap on a build+restart
                )
            log(f"DEPLOY END — exit {proc.returncode}")
        except subprocess.TimeoutExpired:
            log("DEPLOY TIMEOUT — exceeded 1800s; check docker manually")
        except Exception as e:  # noqa: BLE001
            log(f"DEPLOY ERROR — {e}")
    finally:
        _deploying.clear()
        _deploy_lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "deploy-webhook/1"

    def _reply(self, code: int, body: str) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        # Unauthenticated liveness only — reveals nothing, triggers nothing.
        if self.path in ("/healthz", "/health"):
            self._reply(200, "ok\n")
        else:
            self._reply(404, "not found\n")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > _MAX_BODY:
            self._reply(400, "bad length\n")
            return
        raw = self.rfile.read(length)

        if not _signature_ok(raw, self.headers.get("X-Hub-Signature-256")):
            log("rejected: bad/missing signature")
            self._reply(401, "bad signature\n")
            return

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._reply(400, "bad json\n")
            return

        event = self.headers.get("X-GitHub-Event", "")
        deploy, reason = _should_deploy(event, payload)
        log(f"event={event!r} → {reason}")

        if not deploy:
            self._reply(200, f"ack: {reason}\n")
            return
        if _deploying.is_set():
            self._reply(409, "deploy already in progress\n")
            return

        threading.Thread(
            target=_run_deploy, args=(reason,), name="deploy-run", daemon=True,
        ).start()
        self._reply(202, f"deploy accepted: {reason}\n")

    def log_message(self, *args) -> None:  # noqa: D401 — silence default stderr spam
        return


def main() -> int:
    if not SECRET:
        sys.stderr.write(
            "deploy_webhook: DEPLOY_WEBHOOK_SECRET is unset — refusing to start. "
            "This endpoint runs code on the host; it must verify a shared HMAC "
            "secret on every request.\n"
        )
        return 2
    if not Path(DEPLOY_SCRIPT).exists():
        sys.stderr.write(f"deploy_webhook: DEPLOY_SCRIPT not found: {DEPLOY_SCRIPT}\n")
        return 2

    log(
        f"Starting — bind {BIND}:{PORT}, branch {BRANCH!r}, "
        f"repo_pin {REPO_PIN or '(any)'}, script {DEPLOY_SCRIPT}"
    )
    if BIND not in ("127.0.0.1", "localhost", "::1"):
        log(f"WARNING: binding {BIND} (non-loopback) — ensure HMAC + Funnel/firewall")

    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("Interrupted; exiting")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
