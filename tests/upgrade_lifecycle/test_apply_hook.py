"""Tests for app.upgrade_lifecycle.apply_hook (P0#1b).

PROGRAM §63 follow-up. Covers:

  1. Master switch OFF returns immediately
  2. Front-matter parser extracts flat key=value
  3. Front-matter parser returns None when no block present
  4. Front-matter parser tolerates comments + blanks inside the block
  5. Dispatch on bump_requirement → requirements_writer
  6. Dispatch on unknown action returns clear reason
  7. run_one_pass walks the audit log and dispatches matching CRs
  8. Already-processed CRs skipped via idempotency token
  9. Failed dispatch keeps CR in re-try set (not marked processed)
  10. Doc-only CR (no front-matter) is marked processed but not dispatched
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.upgrade_lifecycle import apply_hook as ah


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(tmp_path / "ul"))
    return tmp_path / "ul"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(ah, "_enabled", lambda: True)


# ── 1: Master switch ────────────────────────────────────────────────────


def test_master_switch_off_returns_immediately(isolated_dir, monkeypatch):
    monkeypatch.setattr(ah, "_enabled", lambda: False)
    out = ah.run_one_pass()
    assert out["ok"] is False
    assert out["reason"] == "master_switch_off"


# ── 2-4: Front-matter parser ────────────────────────────────────────────


def test_parse_front_matter_extracts_keys():
    text = (
        "---\n"
        "action: bump_requirement\n"
        "package: starlette\n"
        "from_version: 0.52.1\n"
        "to_version: 1.0.1\n"
        "---\n"
        "# Body markdown\n"
    )
    fm = ah.parse_front_matter(text)
    assert fm == {
        "action": "bump_requirement",
        "package": "starlette",
        "from_version": "0.52.1",
        "to_version": "1.0.1",
    }


def test_parse_front_matter_returns_none_when_absent():
    assert ah.parse_front_matter("# Just markdown\n") is None
    assert ah.parse_front_matter("") is None
    assert ah.parse_front_matter("not even close") is None


def test_parse_front_matter_tolerates_comments_and_blanks():
    text = (
        "---\n"
        "# leading comment\n"
        "\n"
        "action: bump_requirement\n"
        "package: starlette\n"
        "---\n"
        "body\n"
    )
    fm = ah.parse_front_matter(text)
    assert fm is not None
    assert fm["action"] == "bump_requirement"


# ── 5-6: Dispatch ───────────────────────────────────────────────────────


def test_dispatch_routes_bump_requirement_to_writer(isolated_dir, monkeypatch):
    calls = []
    def _fake_apply_bump(*, package, to_version, requestor, reason):
        from app.upgrade_lifecycle.requirements_writer import WriteResult
        calls.append({
            "package": package, "to_version": to_version,
            "requestor": requestor, "reason": reason,
        })
        return WriteResult(ok=True, reason="ok",
                          diff_lines=(f"+{package}=={to_version}",))
    monkeypatch.setattr(
        "app.upgrade_lifecycle.requirements_writer.apply_bump",
        _fake_apply_bump,
    )
    fm = {
        "action": "bump_requirement",
        "package": "starlette", "to_version": "1.0.1",
    }
    res = ah.dispatch(front_matter=fm, cr_id="cr-1234", reason="approved")
    assert res["ok"] is True
    assert len(calls) == 1
    assert calls[0]["package"] == "starlette"
    assert calls[0]["to_version"] == "1.0.1"
    assert calls[0]["requestor"] == "upgrade_lifecycle"


def test_dispatch_unknown_action_returns_clear_reason():
    res = ah.dispatch(
        front_matter={"action": "something_weird"},
        cr_id="cr-1234", reason="approved",
    )
    assert res["ok"] is False
    assert "unknown_action" in res["reason"]


def test_dispatch_missing_package_or_version():
    res = ah.dispatch(
        front_matter={"action": "bump_requirement", "package": "starlette"},
        cr_id="cr-1234", reason="approved",
    )
    assert res["ok"] is False
    assert res["reason"] == "missing_package_or_version"


# ── P0#4: bump_python dispatch ───────────────────────────────────────────


def test_dispatch_routes_bump_python_to_dockerfile_writer(monkeypatch):
    calls: list[dict] = []
    def _fake_apply_bump(*, to_version, from_version, requestor, reason):
        from app.upgrade_lifecycle.dockerfile_writer import WriteResult
        calls.append({
            "to_version": to_version, "from_version": from_version,
            "requestor": requestor, "reason": reason,
        })
        return WriteResult(
            ok=True, reason="ok",
            old_version=from_version or "3.13",
            new_version=to_version,
            sha_pin_dropped=True,
            diff_lines=("+FROM python:3.14-slim",),
        )

    monkeypatch.setattr(
        "app.upgrade_lifecycle.dockerfile_writer.apply_bump",
        _fake_apply_bump,
    )
    # Silence the loud notify side-effect.
    monkeypatch.setattr(ah, "_notify_python_bump_applied", lambda **kw: None)

    fm = {
        "action": "bump_python",
        "from_version": "3.13", "to_version": "3.14",
    }
    res = ah.dispatch(front_matter=fm, cr_id="cr-py", reason="EOL")
    assert res["ok"] is True
    assert res["from_version"] == "3.13"
    assert res["to_version"] == "3.14"
    assert res["sha_pin_dropped"] is True
    assert len(calls) == 1
    assert calls[0]["to_version"] == "3.14"
    assert calls[0]["from_version"] == "3.13"
    assert calls[0]["requestor"] == "upgrade_lifecycle"


def test_dispatch_python_missing_to_version_refused():
    res = ah.dispatch(
        front_matter={"action": "bump_python", "from_version": "3.13"},
        cr_id="cr-py", reason="approved",
    )
    assert res["ok"] is False
    assert res["reason"] == "missing_to_version"


def test_dispatch_python_fires_loud_notification_on_success(monkeypatch):
    """The notify hook is critical=True + arbitrate=False — operator
    sees Python bumps regardless of fatigue caps."""
    notified: list[dict] = []
    from app.upgrade_lifecycle.dockerfile_writer import WriteResult

    monkeypatch.setattr(
        "app.upgrade_lifecycle.dockerfile_writer.apply_bump",
        lambda **kw: WriteResult(
            ok=True, reason="ok",
            old_version="3.13", new_version="3.14",
            sha_pin_dropped=True,
        ),
    )

    def _fake_notify(**kw):
        notified.append(kw)

    monkeypatch.setattr("app.notify.notify", _fake_notify)
    ah._dispatch_python_bump(
        {"action": "bump_python", "from_version": "3.13", "to_version": "3.14"},
        cr_id="cr-py", reason="EOL approaching",
    )
    assert len(notified) == 1
    assert notified[0].get("critical") is True
    assert notified[0].get("arbitrate") is False
    assert "Python 3.13 → 3.14" in notified[0].get("body", "")
    assert "re-pin" in notified[0].get("body", "").lower()


# ── 7: End-to-end pass ──────────────────────────────────────────────────


def test_run_one_pass_dispatches_matching_cr(isolated_dir, enabled, tmp_path,
                                              monkeypatch):
    """Construct a fake audit log + file reader + dispatcher and assert
    the cr gets routed through dispatch."""
    audit_log = tmp_path / "audit.jsonl"
    audit_log.write_text(json.dumps({
        "cr_id": "cr-abc",
        "path": "docs/proposed_upgrades/upgrade_starlette_1_0_1.md",
        "status": "applied",
        "reason": "operator approved",
    }) + "\n")

    body = (
        "---\n"
        "action: bump_requirement\n"
        "package: starlette\n"
        "to_version: 1.0.1\n"
        "---\n"
        "# body\n"
    )

    dispatched: list[dict] = []
    def _fake_dispatcher(*, front_matter, cr_id, reason):
        dispatched.append({"fm": front_matter, "cr_id": cr_id})
        return {"ok": True, "reason": "ok"}

    out = ah.run_one_pass(
        audit_path=audit_log,
        file_reader=lambda path: body,
        bump_dispatcher=_fake_dispatcher,
    )
    assert out["ok"] is True
    assert out["processed"] == 1
    assert len(dispatched) == 1
    assert dispatched[0]["cr_id"] == "cr-abc"


# ── 8: Idempotency ──────────────────────────────────────────────────────


def test_already_processed_cr_skipped(isolated_dir, enabled, tmp_path,
                                       monkeypatch):
    audit_log = tmp_path / "audit.jsonl"
    audit_log.write_text(json.dumps({
        "cr_id": "cr-abc",
        "path": "docs/proposed_upgrades/x.md",
        "status": "applied",
        "reason": "ok",
    }) + "\n")

    # Seed state file with cr-abc already processed
    state_path = ah._state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"processed_cr_ids": ["cr-abc"]}))

    dispatched = []
    out = ah.run_one_pass(
        audit_path=audit_log,
        file_reader=lambda path: "---\naction: bump_requirement\npackage: x\nto_version: 1.0\n---\n",
        bump_dispatcher=lambda **kw: dispatched.append(kw) or {"ok": True},
    )
    assert out["skipped"] >= 1
    assert dispatched == []   # not re-dispatched


# ── 9: Failed dispatch isn't marked processed ───────────────────────────


def test_failed_dispatch_kept_for_retry(isolated_dir, enabled, tmp_path):
    audit_log = tmp_path / "audit.jsonl"
    audit_log.write_text(json.dumps({
        "cr_id": "cr-abc",
        "path": "docs/proposed_upgrades/x.md",
        "status": "applied",
    }) + "\n")

    out = ah.run_one_pass(
        audit_path=audit_log,
        file_reader=lambda path: "---\naction: bump_requirement\npackage: x\nto_version: 1.0\n---\n",
        bump_dispatcher=lambda **kw: {"ok": False, "reason": "simulated"},
    )
    assert out["processed"] == 1
    assert out["errors"] == 1
    # cr-abc NOT in processed set
    state = ah._read_state()
    assert "cr-abc" not in (state.get("processed_cr_ids") or [])


# ── 10: Doc-only CR (no front-matter) ───────────────────────────────────


def test_doc_only_cr_marked_processed_but_not_dispatched(
    isolated_dir, enabled, tmp_path,
):
    audit_log = tmp_path / "audit.jsonl"
    audit_log.write_text(json.dumps({
        "cr_id": "cr-doc",
        "path": "docs/proposed_upgrades/notes.md",
        "status": "applied",
    }) + "\n")

    dispatched = []
    out = ah.run_one_pass(
        audit_path=audit_log,
        file_reader=lambda path: "# Just notes, no front-matter\n",
        bump_dispatcher=lambda **kw: dispatched.append(kw) or {"ok": True},
    )
    assert dispatched == []
    # Marked processed so we don't re-evaluate next pass
    state = ah._read_state()
    assert "cr-doc" in (state.get("processed_cr_ids") or [])


# ── Path filter: non-upgrade CRs ignored ────────────────────────────────


def test_audit_walk_ignores_non_upgrade_paths(isolated_dir, enabled, tmp_path):
    audit_log = tmp_path / "audit.jsonl"
    audit_log.write_text("\n".join(json.dumps(r) for r in [
        {"cr_id": "cr-1", "path": "app/some_other_file.py", "status": "applied"},
        {"cr_id": "cr-2", "path": "docs/proposed_upgrades/x.md", "status": "applied"},
        {"cr_id": "cr-3", "path": "wiki/index.md", "status": "applied"},
    ]) + "\n")

    seen = []
    out = ah.run_one_pass(
        audit_path=audit_log,
        file_reader=lambda path: "---\naction: bump_requirement\npackage: y\nto_version: 2.0\n---\n",
        bump_dispatcher=lambda **kw: seen.append(kw) or {"ok": True},
    )
    # Only cr-2 dispatched
    assert len(seen) == 1
    assert seen[0]["cr_id"] == "cr-2"
