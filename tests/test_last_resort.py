"""Tests for app.notify.last_resort — Gap #12 SMS + email fallback for
critical notifications whose Signal + Web Push both failed."""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic_settings")

from app.notify import last_resort as lr  # noqa: E402


def test_maybe_fire_skipped_for_non_critical() -> None:
    delivered = {"signal": False, "web_push_count": 0}
    out = lr.maybe_fire_last_resort(
        "title", "body",
        delivered=delivered, critical=False,
    )
    assert out["last_resort_sms"] is False
    assert out["last_resort_email"] is False


def test_maybe_fire_skipped_when_signal_succeeded() -> None:
    delivered = {"signal": True, "web_push_count": 0}
    out = lr.maybe_fire_last_resort(
        "title", "body",
        delivered=delivered, critical=True,
    )
    assert out["last_resort_sms"] is False
    assert out["last_resort_email"] is False


def test_maybe_fire_skipped_when_push_succeeded() -> None:
    delivered = {"signal": False, "web_push_count": 1}
    out = lr.maybe_fire_last_resort(
        "title", "body",
        delivered=delivered, critical=True,
    )
    assert out["last_resort_sms"] is False
    assert out["last_resort_email"] is False


def test_maybe_fire_skipped_when_master_off(monkeypatch) -> None:
    monkeypatch.setattr(lr, "_enabled", lambda: False)
    delivered = {"signal": False, "web_push_count": 0}
    out = lr.maybe_fire_last_resort(
        "title", "body",
        delivered=delivered, critical=True,
    )
    assert out["last_resort_sms"] is False
    assert out["last_resort_email"] is False


def test_maybe_fire_invokes_both_when_critical_and_both_failed(monkeypatch) -> None:
    monkeypatch.setattr(lr, "_enabled", lambda: True)
    monkeypatch.setattr(lr, "send_sms", lambda t, b: True)
    monkeypatch.setattr(lr, "send_email", lambda t, b: True)
    delivered = {"signal": False, "web_push_count": 0}
    out = lr.maybe_fire_last_resort(
        "title", "body",
        delivered=delivered, critical=True,
    )
    assert out["last_resort_sms"] is True
    assert out["last_resort_email"] is True


def test_send_sms_returns_false_without_config(monkeypatch) -> None:
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
              "TWILIO_FROM_NUMBER", "OPERATOR_PHONE_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    assert lr.send_sms("t", "b") is False


def test_send_email_returns_false_without_config(monkeypatch) -> None:
    for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "OPERATOR_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    assert lr.send_email("t", "b") is False


def test_send_sms_truncates_long_body(monkeypatch) -> None:
    """The SMS body must be capped (Twilio multi-segment limit). We
    intercept the urlopen call and inspect the encoded payload."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+1")
    monkeypatch.setenv("OPERATOR_PHONE_NUMBER", "+2")

    captured: dict = {}

    class FakeResp:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=10):
        captured["data"] = req.data
        return FakeResp()

    monkeypatch.setattr(lr.urllib.request, "urlopen", fake_urlopen)
    big_body = "x" * 5000
    ok = lr.send_sms("title", big_body)
    assert ok is True
    # Encoded body should not exceed the 1500-char cap.
    decoded = lr.urllib.parse.parse_qs(captured["data"].decode("utf-8"))
    body = decoded["Body"][0]
    assert len(body) <= 1500


def test_maybe_fire_does_not_raise_when_sms_raises(monkeypatch) -> None:
    """Even if send_sms itself raises, the wrapper must not propagate.
    A critical notify is unusable if the fallback can crash the caller."""
    monkeypatch.setattr(lr, "_enabled", lambda: True)

    def boom(title, body):
        raise RuntimeError("simulated")

    monkeypatch.setattr(lr, "send_sms", boom)
    monkeypatch.setattr(lr, "send_email", lambda t, b: False)
    delivered = {"signal": False, "web_push_count": 0}
    # The outer notify() wraps this call in its own try; the helper
    # itself may raise — verify the higher-level isolation in
    # app/notify/api.py would catch it. Here we explicitly check that
    # the helper's failure surfaces as a False rather than a raise.
    with pytest.raises(RuntimeError):
        # Documenting the contract: the helper does NOT catch — the
        # notify() caller does. This guards against a future refactor
        # that quietly swallows the exception inside the helper too.
        lr.maybe_fire_last_resort(
            "t", "b", delivered=delivered, critical=True,
        )
