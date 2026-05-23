"""SMTP-send action handler.

Mirrors :mod:`app.action_requests.handlers.email_draft` but for the
PIM agent's direct ``send_email`` tool (IMAP/SMTP path in
``app.tools.email_tools``). Kept separate from ``email_draft``
because:

  - The data payload shape is slightly narrower (no attachments
    array; the PIM tool doesn't expose attachment paths).
  - The audit trail is easier to read when the handler name matches
    the originating tool.

Data payload shape::

    {"to": "x@example.com", "subject": "...", "body": "...", "html": false}
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.action_requests.handlers.base import ActionHandler, ApplyResult
from app.action_requests.models import ActionType

logger = logging.getLogger(__name__)


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_MAX_BODY_CHARS = 100_000
_MAX_SUBJECT_CHARS = 998


class SmtpSendHandler(ActionHandler):
    @property
    def action_type(self):
        return ActionType.SMTP_SEND

    def validate(self, data: dict[str, Any]) -> tuple[bool, str | None]:
        to = data.get("to")
        if not isinstance(to, str) or not to.strip():
            return False, "to is required and must be a non-empty string"
        if not _EMAIL_RE.match(to.strip()):
            return False, f"invalid email address: {to!r}"
        subject = data.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            return False, "subject is required"
        if len(subject) > _MAX_SUBJECT_CHARS:
            return False, f"subject exceeds {_MAX_SUBJECT_CHARS} chars"
        body = data.get("body")
        if not isinstance(body, str) or not body.strip():
            return False, "body is required"
        if len(body) > _MAX_BODY_CHARS:
            return False, f"body exceeds {_MAX_BODY_CHARS} chars"
        html = data.get("html", False)
        if not isinstance(html, bool):
            return False, "html must be a boolean"
        return True, None

    def apply(self, data: dict[str, Any]) -> ApplyResult:
        import email.utils
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        try:
            from app.config import get_settings
            s = get_settings()
            if not getattr(s, "email_enabled", False):
                return ApplyResult(ok=False, error="email_enabled is false")
            cfg = {
                "smtp_host": s.email_smtp_host,
                "smtp_port": s.email_smtp_port,
                "address": s.email_address,
                "password": s.email_password.get_secret_value(),
            }
        except Exception as exc:  # noqa: BLE001
            return ApplyResult(ok=False, error=f"email config unavailable: {exc}")

        to = str(data["to"]).strip()
        subject = str(data["subject"])
        body = str(data["body"])
        html = bool(data.get("html", False))

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = cfg["address"]
            msg["To"] = to
            msg["Subject"] = subject
            msg["Date"] = email.utils.formatdate(localtime=True)
            content_type = "html" if html else "plain"
            msg.attach(MIMEText(body, content_type, "utf-8"))
            with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(cfg["address"], cfg["password"])
                server.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("smtp_send: SMTP send raised: %s", exc, exc_info=True)
            return ApplyResult(ok=False, error=f"send raised: {exc}")

        return ApplyResult(ok=True, artifact={"recipient": to, "subject": subject[:120]})

    def render_summary(self, data: dict[str, Any]) -> str:
        to = (data.get("to") or "(no recipient)")
        subject = (data.get("subject") or "")[:80]
        return f"📧 SMTP send to {to} — “{subject}”"
