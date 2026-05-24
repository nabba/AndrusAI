#!/usr/bin/env python3
"""external_deadman — Off-host liveness probe with SMS + email escalation.

Gap #1 + #12 (2026-05-24): every in-band monitor of the gateway runs
on the gateway. If the gateway goes silent (host power loss, networking
failure, runaway disk fill, hung event loop), every internal alerting
channel goes silent with it. The operator finds out hours or days later
when the morning briefing doesn't arrive.

This script runs **off** the gateway — on a separate machine, a cloud
cron job, or a tiny VPS. It pings ``$DASHBOARD_URL/health`` on a fixed
cadence. After ``$FAILURE_THRESHOLD`` consecutive failures (default 3),
it sends SMS via Twilio and email via SMTP.

Pure stdlib, single file, ~250 LOC. Runs on any Python 3.9+ host
without ``pip install``. Designed to drop into:

  * A second laptop's launchd / cron.
  * A free-tier cloud cron (GitHub Actions, fly.io schedule, etc.).
  * The operator's phone via Pythonista / Termux.

Configuration via env vars (no flags — cron environments don't like
flag parsing):

    DASHBOARD_URL              https://andrusai.example.com  (required)
    HEALTH_PATH                /health  (default)
    FAILURE_THRESHOLD          3  (default)
    STATE_DIR                  ~/.andrusai_deadman  (state cache)
    HTTP_TIMEOUT_SECONDS       10  (default)

    TWILIO_ACCOUNT_SID         …
    TWILIO_AUTH_TOKEN          …
    TWILIO_FROM_NUMBER         +1…
    OPERATOR_PHONE_NUMBER      +358…

    SMTP_HOST                  smtp.example.com
    SMTP_PORT                  465  (default)
    SMTP_USER                  alerts@example.com
    SMTP_PASSWORD              …
    OPERATOR_EMAIL             andrus@example.com

If neither Twilio nor SMTP env is set, the script logs to stderr and
exits 1 — better visible failure than silent.

Exit codes:
    0 — probe succeeded; alerts (if previously firing) cleared.
    1 — probe failed AND escalated.
    2 — probe failed but threshold not yet hit.
    3 — configuration error (no escalation channels).
"""
from __future__ import annotations

import base64
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path


def _config() -> dict:
    return {
        "dashboard_url": os.environ.get("DASHBOARD_URL", "").strip(),
        "health_path": os.environ.get("HEALTH_PATH", "/health"),
        "failure_threshold": int(os.environ.get("FAILURE_THRESHOLD", "3")),
        "state_dir": Path(
            os.environ.get("STATE_DIR")
            or (Path.home() / ".andrusai_deadman")
        ),
        "http_timeout_seconds": int(os.environ.get("HTTP_TIMEOUT_SECONDS", "10")),
        "twilio_sid": os.environ.get("TWILIO_ACCOUNT_SID", "").strip(),
        "twilio_token": os.environ.get("TWILIO_AUTH_TOKEN", "").strip(),
        "twilio_from": os.environ.get("TWILIO_FROM_NUMBER", "").strip(),
        "operator_phone": os.environ.get("OPERATOR_PHONE_NUMBER", "").strip(),
        "smtp_host": os.environ.get("SMTP_HOST", "").strip(),
        "smtp_port": int(os.environ.get("SMTP_PORT", "465")),
        "smtp_user": os.environ.get("SMTP_USER", "").strip(),
        "smtp_password": os.environ.get("SMTP_PASSWORD", "").strip(),
        "smtp_from": os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", "").strip(),
        "operator_email": os.environ.get("OPERATOR_EMAIL", "").strip(),
    }


def _state_path(cfg: dict) -> Path:
    cfg["state_dir"].mkdir(parents=True, exist_ok=True)
    return cfg["state_dir"] / "state.json"


def _read_state(cfg: dict) -> dict:
    p = _state_path(cfg)
    if not p.exists():
        return {"consecutive_failures": 0, "last_alert_at": None, "last_probe_at": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"consecutive_failures": 0, "last_alert_at": None, "last_probe_at": None}


def _write_state(cfg: dict, state: dict) -> None:
    p = _state_path(cfg)
    p.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _probe(cfg: dict) -> tuple[bool, str]:
    """Return (ok, detail). ``detail`` is the failure mode on False
    so the alert body has useful diagnostic content."""
    url = cfg["dashboard_url"].rstrip("/") + cfg["health_path"]
    try:
        with urllib.request.urlopen(url, timeout=cfg["http_timeout_seconds"]) as resp:
            if 200 <= resp.status < 300:
                return True, f"HTTP {resp.status}"
            return False, f"HTTP {resp.status}"
    except urllib.error.URLError as exc:
        return False, f"URLError: {exc.reason}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _send_sms(cfg: dict, body: str) -> bool:
    if not all([cfg["twilio_sid"], cfg["twilio_token"], cfg["twilio_from"], cfg["operator_phone"]]):
        return False
    data = urllib.parse.urlencode({
        "From": cfg["twilio_from"],
        "To": cfg["operator_phone"],
        "Body": body[:1500],
    }).encode("utf-8")
    creds = base64.b64encode(
        f"{cfg['twilio_sid']}:{cfg['twilio_token']}".encode("utf-8")
    ).decode("ascii")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{cfg['twilio_sid']}/Messages.json"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        print(f"[deadman] SMS failed: {exc}", file=sys.stderr)
        return False


def _send_email(cfg: dict, subject: str, body: str) -> bool:
    if not all([
        cfg["smtp_host"], cfg["smtp_user"], cfg["smtp_password"], cfg["operator_email"]
    ]):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["smtp_from"]
    msg["To"] = cfg["operator_email"]
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], context=ctx, timeout=15) as smtp:
            smtp.login(cfg["smtp_user"], cfg["smtp_password"])
            smtp.send_message(msg)
        return True
    except Exception as exc:
        print(f"[deadman] SMTP failed: {exc}", file=sys.stderr)
        return False


def _format_alert(cfg: dict, state: dict, last_detail: str) -> tuple[str, str]:
    subject = f"[AndrusAI DEAD] {cfg['dashboard_url']} unreachable"
    body = (
        f"AndrusAI dashboard {cfg['dashboard_url']} has failed "
        f"{state['consecutive_failures']} consecutive health probes.\n"
        f"Threshold: {cfg['failure_threshold']}.\n"
        f"Last probe: {datetime.now(timezone.utc).isoformat()}\n"
        f"Last detail: {last_detail}\n\n"
        "Investigation:\n"
        "  1. SSH to the host and check `docker compose ps`.\n"
        "  2. Check tailscale connectivity.\n"
        "  3. Check disk space + workspace mount.\n"
    )
    return subject, body


def _format_recovery(cfg: dict, state: dict) -> tuple[str, str]:
    subject = f"[AndrusAI RECOVERED] {cfg['dashboard_url']} back online"
    body = (
        f"AndrusAI dashboard {cfg['dashboard_url']} is responding again.\n"
        f"Probe completed at {datetime.now(timezone.utc).isoformat()}.\n"
    )
    return subject, body


def main() -> int:
    cfg = _config()
    if not cfg["dashboard_url"]:
        print("[deadman] DASHBOARD_URL is required", file=sys.stderr)
        return 3
    if not any([
        all([cfg["twilio_sid"], cfg["twilio_token"], cfg["twilio_from"], cfg["operator_phone"]]),
        all([cfg["smtp_host"], cfg["smtp_user"], cfg["smtp_password"], cfg["operator_email"]]),
    ]):
        print("[deadman] No escalation channel configured (Twilio or SMTP)", file=sys.stderr)
        return 3

    state = _read_state(cfg)
    ok, detail = _probe(cfg)
    state["last_probe_at"] = datetime.now(timezone.utc).isoformat()

    if ok:
        was_alerting = state["consecutive_failures"] >= cfg["failure_threshold"]
        state["consecutive_failures"] = 0
        if was_alerting:
            subject, body = _format_recovery(cfg, state)
            sms_ok = _send_sms(cfg, body)
            email_ok = _send_email(cfg, subject, body)
            print(f"[deadman] RECOVERED (sms={sms_ok}, email={email_ok})")
        else:
            print(f"[deadman] OK ({detail})")
        _write_state(cfg, state)
        return 0

    # Failure path.
    state["consecutive_failures"] += 1
    threshold = cfg["failure_threshold"]
    if state["consecutive_failures"] < threshold:
        _write_state(cfg, state)
        print(f"[deadman] FAIL #{state['consecutive_failures']}/{threshold} ({detail})")
        return 2

    # At-threshold: escalate (every 6h while the failure persists; the
    # state file remembers we've alerted so subsequent runs in the same
    # outage don't spam — they fire every Nth pass).
    last_alert = state.get("last_alert_at")
    should_alert = True
    if last_alert:
        try:
            last_ts = datetime.fromisoformat(last_alert.replace("Z", "+00:00")).timestamp()
            should_alert = (time.time() - last_ts) > 6 * 3600
        except Exception:
            pass
    if should_alert:
        subject, body = _format_alert(cfg, state, detail)
        sms_ok = _send_sms(cfg, body)
        email_ok = _send_email(cfg, subject, body)
        state["last_alert_at"] = datetime.now(timezone.utc).isoformat()
        print(f"[deadman] ALERT #{state['consecutive_failures']} (sms={sms_ok}, email={email_ok})")
    else:
        print(f"[deadman] FAIL #{state['consecutive_failures']} (alert suppressed; last fired {last_alert})")
    _write_state(cfg, state)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
