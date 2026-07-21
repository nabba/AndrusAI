"""B3-P2 smoke test — `change_requests.apply` works for docs paths.

PROGRAM §63.11. The narrow E2E gap closure from the P2 audit: my
upgrade-lifecycle E2E test stopped at the staging step. This file
adds the missing piece — confirms that
``change_requests.lifecycle.create_request`` →
``change_requests.lifecycle.approve`` → ``change_requests.apply.apply_change``
**actually writes the markdown body to disk** when the target_path
is under ``docs/proposed_upgrades/``.

If `apply_change` ever develops a bug specifically for docs paths
(e.g. a tighter validator policy that adds back a prefix refusal),
this test fails immediately.
"""
from __future__ import annotations

import pytest

# The change_requests package transitively pulls app.config — skip
# when pydantic_settings isn't available (host venv).
pytest.importorskip("pydantic_settings")

from unittest.mock import MagicMock


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Redirect change_requests storage so host runs do not need /app."""
    from app.change_requests import store

    store_dir = tmp_path / "change_requests"
    store_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "_STORE_DIR", store_dir)
    monkeypatch.setattr(store, "_AUDIT_LOG", store_dir / "audit.jsonl")
    store.reset_for_tests()
    yield store


@pytest.fixture
def patched_bridge(monkeypatch):
    """Stub the host bridge so apply_change's write_file + git ops
    succeed without actually shelling out. The fixture also captures
    the write_file call args so the test can assert on what was sent."""
    from app.change_requests import apply as ar_apply

    bridge = MagicMock()
    writes: list[dict] = []

    def _write_file(abs_path, content, create_dirs=False):
        writes.append({
            "abs_path": abs_path,
            "content": content,
            "create_dirs": create_dirs,
        })
        return {"ok": True}

    bridge.write_file = _write_file
    bridge.read_file = lambda p: {"ok": True, "content": ""}
    monkeypatch.setattr(ar_apply, "_get_bridge", lambda: bridge)

    branch_result = MagicMock()
    branch_result.ok = True
    branch_result.error = None
    monkeypatch.setattr(
        ar_apply, "_prepare_git_branch", lambda **kw: branch_result,
    )

    # Git ops — return success without touching git.
    git_result = MagicMock()
    git_result.ok = True
    git_result.commit_sha = "deadbeef"
    git_result.pr_url = "https://github.com/x/y/pull/42"
    git_result.error = None
    monkeypatch.setattr(
        ar_apply, "_run_git_auto_pr", lambda **kw: git_result,
    )

    # Skip module reload (irrelevant for markdown).
    monkeypatch.setattr(
        ar_apply, "_try_module_reload", lambda path: (True, ""),
    )

    return {"bridge": bridge, "writes": writes}


def test_apply_change_writes_docs_proposed_upgrades_file(
    isolated_store, patched_bridge, tmp_path,
):
    """The end-to-end smoke: create_request → approve → apply_change."""
    from app.change_requests.lifecycle import (
        DecisionSource,
        approve,
        create_request,
    )
    from app.change_requests.apply import apply_change

    body = (
        "---\n"
        "action: bump_requirement\n"
        "package: starlette\n"
        "from_version: 0.52.1\n"
        "to_version: 1.0.1\n"
        "---\n"
        "# Operator-accepted upgrade\n"
    )
    cr = create_request(
        requestor="ecosystem_snapshot",
        path="docs/proposed_upgrades/upgrade_starlette_1_0_1.md",
        new_content=body,
        old_content="",
        reason="B3-P2 smoke test",
    )
    # CR must have been created successfully and be PENDING (not
    # rejected at validate time).
    assert cr.id, "create_request did not produce an id"
    from app.change_requests.lifecycle import Status
    assert cr.status == Status.PENDING, (
        f"create_request returned wrong status: {cr.status.value}; "
        f"docs/proposed_upgrades/ should pass validator"
    )

    # Approve.
    approve(cr.id, source=DecisionSource.REACT_APPROVE)

    # Apply.
    result = apply_change(cr.id)
    assert result.ok is True, f"apply failed: {result.error}"

    # write_file was called with the right path + content.
    writes = patched_bridge["writes"]
    assert len(writes) == 1
    assert writes[0]["abs_path"].endswith(
        "docs/proposed_upgrades/upgrade_starlette_1_0_1.md"
    )
    assert writes[0]["content"] == body
    assert writes[0]["create_dirs"] is True
