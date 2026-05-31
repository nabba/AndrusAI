"""Deploy webhook (2026-05-31) — HMAC verification + event routing.

The webhook (`scripts/deploy_webhook.py`) runs `deploy_gateway.sh` on a merge
to the deploy branch. It executes code on the host, so the security-critical
surface is (a) the HMAC signature check and (b) the "is this actually a merge
to the deploy branch from the pinned repo" decision. These pin both.

Loaded via importlib from path with env preset, the same pattern as
`test_gateway_watchdog_cooldown.py`.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import sys
from pathlib import Path

import pytest

SECRET = "testsecret"
REPO = {"full_name": "nabba/andrusai"}


def _load(monkeypatch):
    monkeypatch.setenv("DEPLOY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("DEPLOY_REPO", "nabba/andrusai")
    monkeypatch.setenv("DEPLOY_WEBHOOK_BRANCH", "main")
    path = Path(__file__).parent.parent / "scripts" / "deploy_webhook.py"
    spec = importlib.util.spec_from_file_location("_test_deploy_webhook", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_test_deploy_webhook"] = mod
    spec.loader.exec_module(mod)
    return mod


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


# ── HMAC ───────────────────────────────────────────────────────────────
def test_valid_signature_accepted(monkeypatch):
    mod = _load(monkeypatch)
    body = b'{"hello":1}'
    assert mod._signature_ok(body, _sig(body)) is True


def test_bad_signature_rejected(monkeypatch):
    mod = _load(monkeypatch)
    assert mod._signature_ok(b'{"hello":1}', "sha256=deadbeef") is False


def test_missing_signature_rejected(monkeypatch):
    mod = _load(monkeypatch)
    assert mod._signature_ok(b'{"hello":1}', None) is False


def test_signature_over_wrong_body_rejected(monkeypatch):
    mod = _load(monkeypatch)
    assert mod._signature_ok(b'{"hello":1}', _sig(b"other")) is False


# ── Event routing ──────────────────────────────────────────────────────
def test_push_to_main_deploys(monkeypatch):
    mod = _load(monkeypatch)
    d, _ = mod._should_deploy("push", {"ref": "refs/heads/main", "repository": REPO})
    assert d is True


def test_push_to_other_branch_ignored(monkeypatch):
    mod = _load(monkeypatch)
    d, _ = mod._should_deploy("push", {"ref": "refs/heads/dev", "repository": REPO})
    assert d is False


def test_merged_pr_deploys(monkeypatch):
    mod = _load(monkeypatch)
    d, _ = mod._should_deploy("pull_request", {
        "action": "closed", "number": 5,
        "pull_request": {"merged": True, "base": {"ref": "main"}},
        "repository": REPO,
    })
    assert d is True


def test_closed_unmerged_pr_ignored(monkeypatch):
    mod = _load(monkeypatch)
    d, _ = mod._should_deploy("pull_request", {
        "action": "closed",
        "pull_request": {"merged": False, "base": {"ref": "main"}},
        "repository": REPO,
    })
    assert d is False


def test_merge_into_non_deploy_branch_ignored(monkeypatch):
    mod = _load(monkeypatch)
    d, _ = mod._should_deploy("pull_request", {
        "action": "closed",
        "pull_request": {"merged": True, "base": {"ref": "dev"}},
        "repository": REPO,
    })
    assert d is False


def test_wrong_repo_ignored(monkeypatch):
    mod = _load(monkeypatch)
    d, _ = mod._should_deploy(
        "push", {"ref": "refs/heads/main", "repository": {"full_name": "evil/repo"}},
    )
    assert d is False


def test_ping_acked_not_deployed(monkeypatch):
    mod = _load(monkeypatch)
    d, reason = mod._should_deploy("ping", {"repository": REPO})
    assert d is False and reason == "ping"
