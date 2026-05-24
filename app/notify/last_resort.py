"""last_resort — SMS + email fallback for critical notifications.

Gap #12 (2026-05-24): if Signal AND Web Push both fail, the operator
loses the alert. For ``critical=True`` notifies this is unacceptable —
the whole point of critical is "this matters enough to interrupt."

This module fires when:

  * The notify call had ``critical=True``, AND
  * Signal returned False (delivery failure or signal-cli wedged), AND
  * Web Push fan-out delivered 0 messages, AND
  * The runtime switch ``deadman_last_resort_enabled`` is True.

Two channels, both stdlib-only:

  * **SMS via Twilio** — uses HTTPS Basic-auth POST to the Twilio
    REST API. Requires ``TWILIO_ACCOUNT_SID`` + ``TWILIO_AUTH_TOKEN`` +
    ``TWILIO_FROM_NUMBER`` + ``OPERATOR_PHONE_NUMBER`` env vars.
  * **Email via SMTP** — ``smtplib.SMTP_SSL`` to the configured
    server. Requires ``SMTP_HOST`` + ``SMTP_PORT`` + ``SMTP_USER`` +
    ``SMTP_PASSWORD`` + ``OPERATOR_EMAIL`` env vars.

Both are best-effort. The fallback is logged but never raised — even
the last-resort path must not crash the gateway.

Why not also normal-priority?
=============================

SMS costs money. Email is more permissive but a flood of routine
alerts in the operator's inbox is its own failure mode. Critical-only
keeps the channel meaningful.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger(__name__)


_TWILIO_API_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_deadman_last_resort_enabled
        return get_deadman_last_resort_enabled()
    except Exception:
        return os.getenv(
            "DEADMAN_LAST_RESORT_ENABLED", "true",
        ).lower() in ("true", "1", "yes", "on")


def _twilio_configured() -> bool:
    return all(os.environ.get(k) for k in (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_FROM_NUMBER",
        "OPERATOR_PHONE_NUMBER",
    ))


def _smtp_configured() -> bool:
    return all(os.environ.get(k) for k in (
        "SMTP_HOST",
        "SMTP_USER",
        "SMTP_PASSWORD",
        "OPERATOR_EMAIL",
    ))


def send_sms(title: str, body: str) -> bool:
    """POST to Twilio REST API. Returns True iff the API accepted the
    message (Twilio returns 201 Created). Failure-isolated.

    The SMS body is the title (concise prefix) + first 120 chars of the
    body, suitable for a 160-char SMS segment. Operator opens the
    dashboard to read the rest.
    """
    if not _twilio_configured():
        return False
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    token = os.environ["TWILIO_AUTH_TOKEN"]
    from_number = os.environ["TWILIO_FROM_NUMBER"]
    to_number = os.environ["OPERATOR_PHONE_NUMBER"]

    text = title.strip()
    if body:
        snippet = body.strip().splitlines()[0][:120]
        if snippet:
            text = f"{text}\n{snippet}"
    text = text[:1500]  # Twilio caps multi-segment messages; cap defensively

    data = urllib.parse.urlencode({
        "From": from_number,
        "To": to_number,
        "Body": text,
    }).encode("utf-8")

    creds = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        _TWILIO_API_URL.format(sid=sid),
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
    except Exception:
        logger.warning("last_resort.send_sms: Twilio call failed", exc_info=True)
        return False


def send_email(title: str, body: str) -> bool:
    """SMTP-SSL send. Returns True iff the server accepted. Failure-
    isolated.

    The subject is ``[AndrusAI CRITICAL] {title}`` so an inbox filter
    can foreground it. Body is the full body text, plain.
    """
    if not _smtp_configured():
        return False
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    to_addr = os.environ["OPERATOR_EMAIL"]
    from_addr = os.environ.get("SMTP_FROM", user)

    msg = EmailMessage()
    msg["Subject"] = f"[AndrusAI CRITICAL] {title.strip()}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body or title)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=15) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception:
        logger.warning("last_resort.send_email: SMTP send failed", exc_info=True)
        return False


def maybe_fire_last_resort(
    title: str,
    body: str,
    *,
    delivered: dict,
    critical: bool,
) -> dict:
    """Inspect a ``delivered`` summary; fire SMS + email if and only if:

      * ``critical`` is True,
      * the master switch is on,
      * neither Signal nor Web Push delivered.

    Returns the updated ``delivered`` dict with ``last_resort_sms`` and
    ``last_resort_email`` keys populated for visibility.
    """
    delivered.setdefault("last_resort_sms", False)
    delivered.setdefault("last_resort_email", False)

    if not critical:
        return delivered
    if not _enabled():
        return delivered
    signal_ok = bool(delivered.get("signal"))
    push_ok = int(delivered.get("web_push_count") or 0) > 0
    if signal_ok or push_ok:
        return delivered

    # Both primary channels failed — escalate.
    delivered["last_resort_sms"] = send_sms(title, body)
    delivered["last_resort_email"] = send_email(title, body)
    return delivered


__all__ = [
    "send_sms",
    "send_email",
    "maybe_fire_last_resort",
]
