"""Tests for the heuristic email importance scorer.

Background — added 2026-04-30 after the user asked for "top 25 most
important emails" and got a recency-sorted marketing dump back. The
scorer here gives the rank_emails tool a real signal beyond newest-
first by combining bulk-marker, personal-marker, action-keyword, and
allowlist heuristics.

Tests cover:
  - bulk markers all penalise (List-Unsubscribe, List-ID, noreply,
    Auto-Submitted, Precedence, marketing keywords with cap)
  - personal markers all upweight (direct To:, threaded reply, human
    sender, action keywords with cap)
  - allowlist hit dominates noise
  - state signals (unread, recent) are tiebreaks
  - composition: a marketing blast scores well below a thread reply
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.tools.email_importance import (
    EmailHeaders,
    EmailScore,
    _ALLOWLIST_HIT,
    _BULK_LIST_UNSUBSCRIBE,
    _BULK_LIST_ID,
    _BULK_NOREPLY_SENDER,
    _MARKETING_KEYWORD_CAP,
    _PERSONAL_DIRECT_TO,
    _PERSONAL_THREADED,
    _STATE_UNREAD,
    _is_human_from,
    _matches_allowlist,
    _parse_allowlist,
    score_email,
)


# ── Allowlist parser ─────────────────────────────────────────────────

class TestParseAllowlist:

    def test_empty(self):
        assert _parse_allowlist("") == ()

    def test_single(self):
        assert _parse_allowlist("alice@example.com") == ("alice@example.com",)

    def test_multiple_with_whitespace(self):
        out = _parse_allowlist(" alice@x.com, @acme.com , Bob ")
        assert out == ("alice@x.com", "@acme.com", "bob")

    def test_lowercases(self):
        assert _parse_allowlist("ALICE@X.COM") == ("alice@x.com",)


class TestMatchesAllowlist:

    def test_full_address(self):
        assert _matches_allowlist(
            "Alice <alice@example.com>", ("alice@example.com",)
        ) == "alice@example.com"

    def test_domain(self):
        assert _matches_allowlist(
            "Bob <bob@acme.com>", ("@acme.com",)
        ) == "@acme.com"

    def test_name_fragment(self):
        assert _matches_allowlist(
            "Charlie Smith <charlie@x.com>", ("charlie",)
        ) == "charlie"

    def test_no_match(self):
        assert _matches_allowlist("dave@y.com", ("alice@x.com",)) is None

    def test_empty_inputs(self):
        assert _matches_allowlist("", ("alice@x.com",)) is None
        assert _matches_allowlist("alice@x.com", ()) is None


# ── Human-sender detector ────────────────────────────────────────────

class TestIsHumanFrom:

    def test_human_with_name_and_address(self):
        assert _is_human_from("Alice Smith <alice@example.com>")

    def test_address_only_is_not(self):
        assert not _is_human_from("alice@example.com")

    def test_noreply_is_not_human(self):
        assert not _is_human_from("Marketing <noreply@x.com>")
        assert not _is_human_from("Alerts <do-not-reply@y.com>")

    def test_bot_display_names(self):
        assert not _is_human_from("Notifications <notify@x.com>")
        assert not _is_human_from("Alerts <alerts@x.com>")
        assert not _is_human_from("Automated <auto@x.com>")

    def test_empty(self):
        assert not _is_human_from("")


# ── score_email composition ──────────────────────────────────────────

class TestScoreEmail:
    """Composition tests — a real marketing blast should score well
    below a real thread reply, regardless of recency."""

    def _ts(self, hours_ago: float) -> datetime:
        return datetime.now(timezone.utc) - timedelta(hours=hours_ago)

    def test_marketing_blast_scores_negative(self):
        h = EmailHeaders(
            from_="DealMaster <noreply@deals.example.com>",
            to="user@example.com",
            cc="",
            subject="🎉 50% OFF — Limited Time! Shop Now!",
            list_unsubscribe="<mailto:unsub@x.com>",
            list_id="<deals.example.com>",
            date=self._ts(2),
            unread=True,
        )
        s = score_email(h, user_address="user@example.com")
        # Should be deeply negative: noreply (-2) + List-Unsub (-3) +
        # List-ID (-2) + marketing kw cap (-3) = -10, partly offset by
        # direct To (+1), unread (+1), recent (+0.5) = -7.5.
        assert s.score < -3
        assert any("List-Unsubscribe" in r for r in s.reasons)
        assert any("noreply" in r for r in s.reasons)

    def test_thread_reply_scores_positive(self):
        h = EmailHeaders(
            from_="Alice Smith <alice@example.com>",
            to="user@example.com",
            cc="",
            subject="Re: Q3 budget review — please confirm by Friday",
            in_reply_to="<msg-1@example.com>",
            references="<msg-1@example.com>",
            date=self._ts(1),
            unread=True,
        )
        s = score_email(h, user_address="user@example.com")
        # Direct (+1) + threaded (+2) + human (+1) + action kw "review"
        # / "please" / "confirm" hits cap (+3) + unread (+1) +
        # recent (+0.5) = +8.5
        assert s.score >= 5
        assert any("thread reply" in r for r in s.reasons)

    def test_allowlist_hit_dominates_noise(self):
        """An allowlisted sender should rank above ordinary email even
        if otherwise unremarkable."""
        h = EmailHeaders(
            from_="Boss <boss@acme.com>",
            to="user@example.com",
            cc="",
            subject="quick check-in",
            date=self._ts(0.5),
        )
        s = score_email(
            h, user_address="user@example.com",
            important_senders=("@acme.com",),
        )
        # +5 allowlist + 1 direct + 1 human + 0.5 recent ≈ 7.5
        assert s.score > 5
        assert any("allowlisted" in r for r in s.reasons)

    def test_cc_only_does_not_get_direct_bonus(self):
        h = EmailHeaders(
            from_="Alice <alice@x.com>",
            to="bob@x.com",
            cc="user@example.com",
            subject="FYI",
            date=self._ts(1),
        )
        s = score_email(h, user_address="user@example.com")
        assert not any("direct To" in r for r in s.reasons)

    def test_marketing_keyword_cap(self):
        """A subject crammed with marketing words should not produce
        runaway penalty — capped at _MARKETING_KEYWORD_CAP."""
        h = EmailHeaders(
            from_="Promo <noreply@p.com>",
            subject="SALE DEAL DISCOUNT FREE SHIPPING LIMITED TIME EXCLUSIVE",
            date=self._ts(0),
        )
        s = score_email(h, user_address="user@example.com")
        # Find the marketing-keyword reason
        mkt = [r for r in s.reasons if "marketing keywords" in r]
        assert len(mkt) == 1
        # Total marketing-keyword penalty must equal the cap
        assert _MARKETING_KEYWORD_CAP in [
            int(r.split()[0]) for r in s.reasons if "marketing keywords" in r
        ]

    def test_action_keyword_cap_applies(self):
        """Action keywords also capped — important emails shouldn't
        runaway just by stacking 'urgent please review confirm now'."""
        h = EmailHeaders(
            from_="Alice <alice@x.com>",
            to="user@example.com",
            subject="URGENT please review approve confirm action required",
            date=self._ts(0),
        )
        s = score_email(h, user_address="user@example.com")
        ak = [r for r in s.reasons if "action keywords" in r]
        assert len(ak) == 1
        # Sign in reason matches the cap
        val = int(ak[0].split()[0])
        assert 1 <= val <= 3

    def test_unread_recent_tiebreak(self):
        """Two otherwise-identical emails: the unread+recent one should
        score strictly higher (small but non-zero margin)."""
        base = dict(
            from_="Alice <alice@x.com>",
            to="user@example.com",
            subject="hi",
        )
        h_old = EmailHeaders(**base, date=self._ts(48), unread=False)
        h_new = EmailHeaders(**base, date=self._ts(1), unread=True)
        s_old = score_email(h_old, user_address="user@example.com")
        s_new = score_email(h_new, user_address="user@example.com")
        assert s_new.score > s_old.score

    def test_no_user_address_does_not_crash(self):
        """If user_address is missing, the scorer should still produce
        a sensible result (just no direct-To bonus)."""
        h = EmailHeaders(
            from_="Alice <alice@x.com>",
            to="someone@x.com",
            subject="hi",
            date=self._ts(1),
        )
        s = score_email(h, user_address="")
        # Should not raise and score should be finite
        assert isinstance(s.score, (int, float))
        # And no direct-To reason
        assert not any("direct To" in r for r in s.reasons)


class TestScoreOrdering:
    """End-to-end: score a small fixture set and verify rank order
    matches what a human triage expert would expect."""

    def _ts(self, hours_ago: float) -> datetime:
        return datetime.now(timezone.utc) - timedelta(hours=hours_ago)

    def test_real_world_inbox_orders_correctly(self):
        emails = {
            "marketing": EmailHeaders(
                from_="DealMaster <noreply@x.com>",
                to="user@example.com",
                subject="🎉 50% OFF — Limited Time!",
                list_unsubscribe="<mailto:u@x.com>",
                list_id="<deals.x.com>",
                date=self._ts(0.5),  # newest
                unread=True,
            ),
            "thread_reply": EmailHeaders(
                from_="Alice <alice@example.com>",
                to="user@example.com",
                subject="Re: Q3 budget — please review",
                in_reply_to="<m1@x.com>",
                date=self._ts(4),
                unread=True,
            ),
            "boss_allowlist": EmailHeaders(
                from_="Boss <boss@acme.com>",
                to="user@example.com",
                subject="catch up tomorrow?",
                date=self._ts(8),
                unread=False,
            ),
            "system_notification": EmailHeaders(
                from_="GitHub <notifications@github.com>",
                to="user@example.com",
                subject="[PR #42] please review",
                list_id="<github.com>",
                auto_submitted="auto-generated",
                date=self._ts(2),
                unread=True,
            ),
        }
        ranked = sorted(
            emails.items(),
            key=lambda kv: -score_email(
                kv[1],
                user_address="user@example.com",
                important_senders=("@acme.com",),
            ).score,
        )
        order = [name for name, _ in ranked]
        # Boss (allowlist) should be #1 or #2
        assert order.index("boss_allowlist") <= 1
        # Marketing should be last
        assert order[-1] == "marketing"
        # Thread reply should beat system notification
        assert order.index("thread_reply") < order.index("system_notification")
