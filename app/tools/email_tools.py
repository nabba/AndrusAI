"""
email_tools.py — Email management via IMAP/SMTP (Python stdlib).

Zero external dependencies. Uses imaplib, smtplib, and email modules.

Configuration: set EMAIL_* env vars in .env (see config.py).

Usage:
    from app.tools.email_tools import create_email_tools
    tools = create_email_tools("pim")
"""

import email
import email.utils
import imaplib
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _get_email_config() -> dict | None:
    """Load email config. Returns None if not configured."""
    try:
        from app.config import get_settings
        s = get_settings()
        if not s.email_enabled:
            return None
        if not s.email_imap_host or not s.email_address:
            logger.debug("email_tools: email not fully configured")
            return None
        return {
            "imap_host": s.email_imap_host,
            "imap_port": s.email_imap_port,
            "smtp_host": s.email_smtp_host,
            "smtp_port": s.email_smtp_port,
            "address": s.email_address,
            "password": s.email_password.get_secret_value(),
        }
    except Exception:
        return None


def _connect_imap(cfg: dict) -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP connection."""
    conn = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
    conn.login(cfg["address"], cfg["password"])
    return conn


def _decode_header(raw: str) -> str:
    """Decode RFC 2047 encoded email header."""
    parts = email.header.decode_header(raw)
    decoded = []
    for data, charset in parts:
        if isinstance(data, bytes):
            decoded.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(data)
    return " ".join(decoded)


def _extract_body(msg: email.message.Message, max_chars: int = 4000) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")[:max_chars]
        # Fallback to HTML
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    text = payload.decode("utf-8", errors="replace")
                    # Basic HTML strip
                    import re
                    text = re.sub(r"<[^>]+>", " ", text)
                    return text[:max_chars]
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")[:max_chars]
    return "(no text body)"


def create_email_tools(agent_id: str) -> list:
    """Create email tools. Returns [] if email is not configured."""
    cfg = _get_email_config()
    if not cfg:
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    # ── Tool definitions ──────────────────────────────────────────

    class _CheckEmailInput(BaseModel):
        folder: str = Field(default="INBOX", description="Mailbox folder to check")
        limit: int = Field(default=10, description="Max number of emails to return")
        unread_only: bool = Field(default=False, description="Only show unread emails")
        hours_back: int = Field(
            default=0,
            description=(
                "If >0, only count/list emails received within the last N hours. "
                "Use this for queries like 'emails from last 3 hours'."
            ),
        )
        days_back: int = Field(
            default=0,
            description=(
                "If >0, only count/list emails received within the last N days. "
                "Use this for queries like 'emails from last week'."
            ),
        )
        from_sender: str = Field(
            default="",
            description=(
                "If set, only count/list emails whose From header contains this "
                "string. Can be a full address (alice@example.com), a domain "
                "(@example.com), or a name fragment (Alice). Case-insensitive."
            ),
        )
        subject_contains: str = Field(
            default="",
            description=(
                "If set, only count/list emails whose Subject contains this "
                "string (case-insensitive IMAP SUBJECT search)."
            ),
        )
        count_only: bool = Field(
            default=False,
            description=(
                "If True, return just the count (e.g. '7 emails'). Faster than "
                "listing. Use for 'how many emails...' queries."
            ),
        )

    class CheckEmailTool(BaseTool):
        name: str = "check_email"
        description: str = (
            "Check email inbox. Supports time-window filtering (hours_back / "
            "days_back), sender filtering (from_sender), subject filtering "
            "(subject_contains), and count-only mode (count_only). Returns "
            "matching emails with sender, subject, date."
        )
        args_schema: Type[BaseModel] = _CheckEmailInput

        def _run(
            self, folder: str = "INBOX", limit: int = 10,
            unread_only: bool = False, hours_back: int = 0,
            days_back: int = 0, from_sender: str = "",
            subject_contains: str = "", count_only: bool = False,
        ) -> str:
            try:
                conn = _connect_imap(cfg)
                conn.select(folder, readonly=True)

                # Build IMAP search criteria.  "SINCE <date>" filters by day;
                # for sub-day windows we post-filter on the Date header.
                criteria_parts = []
                if unread_only:
                    criteria_parts.append("UNSEEN")
                since_dt: datetime | None = None
                if hours_back > 0:
                    since_dt = datetime.now() - timedelta(hours=hours_back)
                    # IMAP SINCE is day-granular — use the day of since_dt
                    criteria_parts.append(f'SINCE "{since_dt.strftime("%d-%b-%Y")}"')
                elif days_back > 0:
                    since_dt = datetime.now() - timedelta(days=days_back)
                    criteria_parts.append(f'SINCE "{since_dt.strftime("%d-%b-%Y")}"')
                if from_sender:
                    # IMAP FROM is a substring match on the From header
                    safe_sender = from_sender.replace('"', '\\"')
                    criteria_parts.append(f'FROM "{safe_sender}"')
                if subject_contains:
                    safe_subj = subject_contains.replace('"', '\\"')
                    criteria_parts.append(f'SUBJECT "{safe_subj}"')
                criteria = " ".join(criteria_parts) if criteria_parts else "ALL"

                _, msg_nums = conn.search(None, criteria)
                nums = msg_nums[0].split()

                # Post-filter for sub-day windows (hours_back): parse Date header
                if hours_back > 0 and since_dt and nums:
                    filtered = []
                    for num in nums:
                        _, data = conn.fetch(num, "(BODY.PEEK[HEADER.FIELDS (DATE)])")
                        if not data or not data[0]:
                            continue
                        raw = data[0][1]
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        date_line = raw.strip().replace("Date: ", "", 1).strip()
                        try:
                            msg_dt = email.utils.parsedate_to_datetime(date_line)
                            # Normalize to naive local time for comparison
                            if msg_dt.tzinfo is not None:
                                msg_dt = msg_dt.astimezone().replace(tzinfo=None)
                            if msg_dt >= since_dt:
                                filtered.append(num)
                        except Exception:
                            continue
                    nums = filtered

                def _scope_desc() -> str:
                    parts = []
                    if from_sender:
                        parts.append(f"from '{from_sender}'")
                    if subject_contains:
                        parts.append(f"with subject containing '{subject_contains}'")
                    if hours_back > 0:
                        parts.append(f"in the last {hours_back} hour(s)")
                    elif days_back > 0:
                        parts.append(f"in the last {days_back} day(s)")
                    return (" " + " ".join(parts)) if parts else ""

                if not nums:
                    conn.close()
                    conn.logout()
                    return f"No {'unread ' if unread_only else ''}emails found{_scope_desc()}."

                if count_only:
                    conn.close()
                    conn.logout()
                    label = "unread " if unread_only else ""
                    return f"{len(nums)} {label}email(s){_scope_desc()}."

                # Take the most recent N
                recent = nums[-limit:]
                results = []
                for num in reversed(recent):
                    _, data = conn.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                    if not data or not data[0]:
                        continue
                    raw = data[0][1]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    msg = email.message_from_string(raw)
                    from_addr = _decode_header(msg.get("From", "unknown"))
                    subject = _decode_header(msg.get("Subject", "(no subject)"))
                    date = msg.get("Date", "")
                    results.append(f"  From: {from_addr}\n  Subject: {subject}\n  Date: {date}")

                conn.close()
                conn.logout()
                total = len(nums)
                shown = len(results)
                count_label = f"{total} email(s){_scope_desc()}"
                if shown < total:
                    count_label += f" (showing most recent {shown})"
                return f"{count_label}:\n\n" + "\n\n".join(results)
            except Exception as e:
                return f"Error checking email: {str(e)[:300]}"

    class _ReadEmailInput(BaseModel):
        subject_query: str = Field(description="Subject line to search for (partial match)")
        folder: str = Field(default="INBOX", description="Mailbox folder")

    class ReadEmailTool(BaseTool):
        name: str = "read_email"
        description: str = (
            "Read the full content of an email by searching for its subject. "
            "Returns the email body and attachment names."
        )
        args_schema: Type[BaseModel] = _ReadEmailInput

        def _run(self, subject_query: str, folder: str = "INBOX") -> str:
            try:
                conn = _connect_imap(cfg)
                conn.select(folder, readonly=True)
                _, msg_nums = conn.search(None, "SUBJECT", f'"{subject_query}"')
                nums = msg_nums[0].split()
                if not nums:
                    conn.close()
                    conn.logout()
                    return f"No email found matching subject: {subject_query}"

                num = nums[-1]  # Most recent match
                _, data = conn.fetch(num, "(RFC822)")
                raw = data[0][1]
                msg = email.message_from_bytes(raw)

                from_addr = _decode_header(msg.get("From", "unknown"))
                subject = _decode_header(msg.get("Subject", "(no subject)"))
                date = msg.get("Date", "")
                body = _extract_body(msg)

                # List attachments
                attachments = []
                if msg.is_multipart():
                    for part in msg.walk():
                        fn = part.get_filename()
                        if fn:
                            attachments.append(_decode_header(fn))

                conn.close()
                conn.logout()

                result = f"From: {from_addr}\nSubject: {subject}\nDate: {date}\n\n{body}"
                if attachments:
                    result += f"\n\nAttachments: {', '.join(attachments)}"
                return result
            except Exception as e:
                return f"Error reading email: {str(e)[:300]}"

    class _SendEmailInput(BaseModel):
        to: str = Field(description="Recipient email address")
        subject: str = Field(description="Email subject line")
        body: str = Field(description="Email body text")
        html: bool = Field(default=False, description="Send as HTML email")

    class SendEmailTool(BaseTool):
        name: str = "send_email"
        description: str = (
            "Send an email via SMTP. Provide recipient, subject, and body."
        )
        args_schema: Type[BaseModel] = _SendEmailInput

        def _run(self, to: str, subject: str, body: str, html: bool = False) -> str:
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

                return f"Email sent to {to}: {subject}"
            except Exception as e:
                return f"Error sending email: {str(e)[:300]}"

    class _SearchEmailInput(BaseModel):
        query: str = Field(
            default="",
            description=(
                "Subject keyword to search for (IMAP SUBJECT match). Optional "
                "if from_sender is specified."
            ),
        )
        from_sender: str = Field(
            default="",
            description=(
                "If set, only match emails whose From header contains this "
                "string (full address, domain, or name fragment)."
            ),
        )
        folder: str = Field(default="INBOX", description="Mailbox folder")
        days_back: int = Field(default=30, description="Search within last N days")

    class SearchEmailTool(BaseTool):
        name: str = "search_email"
        description: str = (
            "Search emails by subject keyword and/or sender. Pass query for "
            "subject match, from_sender for sender match, or both. "
            "Returns matching emails from the specified time period."
        )
        args_schema: Type[BaseModel] = _SearchEmailInput

        def _run(
            self, query: str = "", from_sender: str = "",
            folder: str = "INBOX", days_back: int = 30,
        ) -> str:
            if not query and not from_sender:
                return "search_email: provide at least one of 'query' or 'from_sender'."
            try:
                conn = _connect_imap(cfg)
                conn.select(folder, readonly=True)

                since = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
                criteria = [f"SINCE {since}"]
                if query:
                    safe_q = query.replace('"', '\\"')
                    criteria.append(f'SUBJECT "{safe_q}"')
                if from_sender:
                    safe_s = from_sender.replace('"', '\\"')
                    criteria.append(f'FROM "{safe_s}"')
                criteria_str = "(" + " ".join(criteria) + ")"
                _, msg_nums = conn.search(None, criteria_str)
                nums = msg_nums[0].split()

                if not nums:
                    conn.close()
                    conn.logout()
                    scope = []
                    if query:
                        scope.append(f"subject '{query}'")
                    if from_sender:
                        scope.append(f"from '{from_sender}'")
                    return f"No emails matching {' and '.join(scope)} in last {days_back} days."

                results = []
                for num in reversed(nums[-20:]):  # Max 20 results
                    _, data = conn.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                    if not data or not data[0]:
                        continue
                    raw = data[0][1]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    msg_header = email.message_from_string(raw)
                    from_addr = _decode_header(msg_header.get("From", "unknown"))
                    subj = _decode_header(msg_header.get("Subject", "(no subject)"))
                    date = msg_header.get("Date", "")
                    results.append(f"  From: {from_addr}\n  Subject: {subj}\n  Date: {date}")

                conn.close()
                conn.logout()
                return f"Found {len(results)} match(es):\n\n" + "\n\n".join(results)
            except Exception as e:
                return f"Error searching email: {str(e)[:300]}"

    class _OrganizeInput(BaseModel):
        subject_query: str = Field(description="Subject to match")
        action: str = Field(
            description="Action: 'mark_read', 'mark_unread', 'archive', 'move'"
        )
        target_folder: str = Field(
            default="",
            description="Target folder for 'move' action",
        )

    class OrganizeEmailTool(BaseTool):
        name: str = "organize_email"
        description: str = (
            "Organize emails: mark as read/unread, archive, or move to folder."
        )
        args_schema: Type[BaseModel] = _OrganizeInput

        def _run(self, subject_query: str, action: str, target_folder: str = "") -> str:
            try:
                conn = _connect_imap(cfg)
                conn.select("INBOX")
                _, msg_nums = conn.search(None, "SUBJECT", f'"{subject_query}"')
                nums = msg_nums[0].split()
                if not nums:
                    conn.close()
                    conn.logout()
                    return f"No email found matching: {subject_query}"

                num = nums[-1]
                if action == "mark_read":
                    conn.store(num, "+FLAGS", "\\Seen")
                elif action == "mark_unread":
                    conn.store(num, "-FLAGS", "\\Seen")
                elif action == "archive":
                    conn.copy(num, "[Gmail]/All Mail")
                    conn.store(num, "+FLAGS", "\\Deleted")
                    conn.expunge()
                elif action == "move" and target_folder:
                    conn.copy(num, target_folder)
                    conn.store(num, "+FLAGS", "\\Deleted")
                    conn.expunge()
                else:
                    conn.close()
                    conn.logout()
                    return f"Invalid action: {action}"

                conn.close()
                conn.logout()
                return f"Email '{subject_query}': {action} completed."
            except Exception as e:
                return f"Error organizing email: {str(e)[:300]}"

    # ── rank_emails: heuristic importance ranking ─────────────────
    #
    # Added 2026-04-30 to give the PIM agent a real signal beyond
    # recency when the user asks "rank my top N most important emails".
    # The scorer in app.tools.email_importance combines bulk markers,
    # personal/threaded markers, action keywords, and an env-driven
    # allowlist. Output includes per-email reasons so the agent can
    # explain *why* something ranked where it did.

    class _RankEmailsInput(BaseModel):
        folder: str = Field(default="INBOX", description="Mailbox folder to rank")
        top_n: int = Field(
            default=10,
            description=(
                "Number of top-ranked emails to return. Cap at 50 to "
                "keep the LLM-readable summary compact."
            ),
        )
        hours_back: int = Field(
            default=0,
            description=(
                "If >0, only consider emails received within the last N "
                "hours. Use this for queries like 'most important emails "
                "in the last 6 hours'."
            ),
        )
        days_back: int = Field(
            default=1,
            description=(
                "If hours_back is 0, look back this many days (default 1 "
                "= yesterday + today). Set to 7 for weekly digests etc."
            ),
        )
        include_reasons: bool = Field(
            default=True,
            description=(
                "If True, append a one-line reason summary after each "
                "ranked email so the user/agent can audit the ranking."
            ),
        )

    class RankEmailsTool(BaseTool):
        name: str = "rank_emails"
        description: str = (
            "Rank inbox emails by HEURISTIC IMPORTANCE (not recency). "
            "Combines bulk-marker analysis (List-Unsubscribe, noreply "
            "senders, marketing keywords) with personal-marker analysis "
            "(direct To:, threaded reply, action keywords like 'urgent') "
            "and an env-curated allowlist (EMAIL_IMPORTANT_SENDERS). "
            "USE THIS for queries like 'top N most important emails', "
            "'rank emails by importance', or 'what should I read first'. "
            "Returns top_n emails sorted by score, with optional reasons "
            "explaining the rank."
        )
        args_schema: Type[BaseModel] = _RankEmailsInput

        def _run(
            self, folder: str = "INBOX", top_n: int = 10,
            hours_back: int = 0, days_back: int = 1,
            include_reasons: bool = True,
        ) -> str:
            from app.tools.email_importance import (
                EmailHeaders, score_email, _parse_allowlist,
            )
            from app.config import get_settings
            try:
                top_n = max(1, min(50, int(top_n)))
                conn = _connect_imap(cfg)
                conn.select(folder, readonly=True)

                # Build a SINCE window. Hours-back uses day granularity in
                # IMAP, then post-filters on the Date header for precision.
                since_dt: datetime | None = None
                if hours_back > 0:
                    since_dt = datetime.now() - timedelta(hours=hours_back)
                elif days_back > 0:
                    since_dt = datetime.now() - timedelta(days=days_back)
                criteria = "ALL"
                if since_dt is not None:
                    criteria = f'SINCE "{since_dt.strftime("%d-%b-%Y")}"'
                _, msg_nums = conn.search(None, criteria)
                nums = msg_nums[0].split()
                if not nums:
                    conn.close()
                    conn.logout()
                    return "No emails to rank in the requested window."

                # Cap how many messages we fetch headers for. 200 is plenty
                # for daily / weekly triage and bounds IMAP cost.
                _MAX_FETCH = 200
                if len(nums) > _MAX_FETCH:
                    nums = nums[-_MAX_FETCH:]

                # Pull unread set in one shot so we don't hit IMAP per row
                unread_set: set[bytes] = set()
                try:
                    _, _u = conn.search(None, "UNSEEN")
                    unread_set = set(_u[0].split())
                except Exception:
                    pass

                user_address = cfg.get("address", "")
                allowlist = _parse_allowlist(
                    get_settings().email_important_senders
                )

                rows: list[tuple[float, list[str], str, str, str]] = []
                # Each row: (score, reasons, from, subject, date_str)

                for num in reversed(nums):
                    _, data = conn.fetch(
                        num,
                        "(BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE "
                        "LIST-UNSUBSCRIBE LIST-ID AUTO-SUBMITTED PRECEDENCE "
                        "IN-REPLY-TO REFERENCES)])",
                    )
                    if not data or not data[0]:
                        continue
                    raw = data[0][1]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    msg_h = email.message_from_string(raw)

                    # Post-filter for sub-day windows
                    msg_dt = None
                    date_raw = msg_h.get("Date", "")
                    try:
                        msg_dt = email.utils.parsedate_to_datetime(date_raw)
                    except Exception:
                        pass
                    if since_dt is not None and msg_dt is not None:
                        # Normalise both to naive local for comparison
                        msg_dt_cmp = msg_dt
                        if msg_dt_cmp.tzinfo is not None:
                            msg_dt_cmp = msg_dt_cmp.astimezone().replace(tzinfo=None)
                        if msg_dt_cmp < since_dt:
                            continue

                    headers = EmailHeaders(
                        from_=_decode_header(msg_h.get("From", "")),
                        to=_decode_header(msg_h.get("To", "")),
                        cc=_decode_header(msg_h.get("Cc", "")),
                        subject=_decode_header(msg_h.get("Subject", "")),
                        list_unsubscribe=msg_h.get("List-Unsubscribe"),
                        list_id=msg_h.get("List-ID"),
                        auto_submitted=msg_h.get("Auto-Submitted"),
                        precedence=msg_h.get("Precedence"),
                        in_reply_to=msg_h.get("In-Reply-To"),
                        references=msg_h.get("References"),
                        date=msg_dt,
                        unread=(num in unread_set),
                    )
                    s = score_email(
                        headers,
                        user_address=user_address,
                        important_senders=allowlist,
                    )
                    rows.append((
                        s.score, s.reasons,
                        headers.from_ or "(unknown)",
                        headers.subject or "(no subject)",
                        date_raw,
                    ))

                conn.close()
                conn.logout()

                if not rows:
                    return "No emails to rank in the requested window."

                # Sort by score desc; tiebreak by recency (date string is
                # not strictly orderable but rows already came in newest-
                # first so a stable sort preserves that on equal scores).
                rows.sort(key=lambda r: r[0], reverse=True)
                out_rows = rows[:top_n]

                window_desc = (
                    f"last {hours_back}h" if hours_back > 0
                    else f"last {days_back}d" if days_back > 0
                    else "inbox"
                )
                lines = [
                    f"Top {len(out_rows)} most important email(s) "
                    f"({window_desc}, {len(rows)} considered):",
                    "",
                ]
                for i, (score, reasons, frm, subj, date) in enumerate(out_rows, 1):
                    lines.append(f"{i}. [score={score:+.1f}] {frm}")
                    lines.append(f"   Subject: {subj}")
                    lines.append(f"   Date: {date}")
                    if include_reasons and reasons:
                        # Cap reason list so output stays compact for LLM
                        rs = ", ".join(reasons[:6])
                        if len(reasons) > 6:
                            rs += f" (+{len(reasons)-6} more)"
                        lines.append(f"   Why: {rs}")
                    lines.append("")
                return "\n".join(lines).rstrip()
            except Exception as e:
                return f"Error ranking emails: {str(e)[:300]}"

    return [
        CheckEmailTool(),
        ReadEmailTool(),
        SendEmailTool(),
        SearchEmailTool(),
        OrganizeEmailTool(),
        RankEmailsTool(),
    ]
