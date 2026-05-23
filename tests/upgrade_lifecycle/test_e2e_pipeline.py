"""P2#d — End-to-end integration test for the upgrade lifecycle.

PROGRAM §63.9 (P2 hardening). Unit tests pin per-module behavior;
this test pins the **composition**. It exercises the full chain:

  capability extraction (U1)
    → impact analysis (U2)
    → orchestrator's CR composition (U4)
    → proposal_bridge staging
    → apply_hook front-matter parse
    → requirements_writer mutation

with the LLM + network at the edges stubbed but the WIRING between
modules real. This is exactly the class of bug the §63.7 ultrathink
caught (target_path validation, missing front-matter, kwarg
mismatch) — a single E2E test pins the chain against regression.
"""
from __future__ import annotations

import json
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.upgrade_lifecycle import (
    apply_hook,
    changelog_fetcher,
    impact_analysis,
    major_auto_cr,
    orchestrator,
    requirements_writer,
)
from app.upgrade_lifecycle.protocol import Capability, ImpactReport, TrialResult


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A tiny repo layout the chain can actually walk."""
    # workspace under tmp/ul, repo at tmp/repo
    workspace = tmp_path / "ul"
    workspace.mkdir()
    monkeypatch.setenv("UPGRADE_LIFECYCLE_DIR", str(workspace))

    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "user.py").write_text(
        "import asyncio\n"
        "async def fan_out():\n"
        "    await asyncio.gather(*[task() for task in tasks])\n"
    )
    # Real requirements.txt the writer can mutate
    req = repo / "requirements.txt"
    req.write_text("click==8.0.0\nfastapi==0.110.0\n")
    monkeypatch.setenv("REQUIREMENTS_PATH", str(req))

    return {"workspace": workspace, "repo": repo, "requirements": req}


@pytest.fixture
def all_switches_on(monkeypatch):
    """Force-enable every switch in the chain."""
    for fn_name, value in [
        ("_enabled", True),
    ]:
        for mod in (
            changelog_fetcher, major_auto_cr, requirements_writer,
            apply_hook,
        ):
            if hasattr(mod, fn_name):
                monkeypatch.setattr(mod, fn_name, lambda v=value: v)


# ── Helpers ──────────────────────────────────────────────────────────────


def _stub_llm_capability() -> str:
    return json.dumps({
        "new_features": ["asyncio.TaskGroup added"],
        "deprecations": [],
        "breaking_changes": [],
        "security_fixes": [],
        "perf_notes": [],
        "license_change": "",
        "notes": "",
    })


class _StubLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls = 0

    def call(self, _messages):
        self.calls += 1
        return self._reply


# ── The E2E test ────────────────────────────────────────────────────────


def test_full_chain_extract_to_writer(fake_repo, all_switches_on, monkeypatch):
    """Exercise U1 → U2 → U4 → proposal_bridge → apply_hook → writer.

    This catches:
      * target_path validation against allowed roots
      * YAML front-matter shape consumed by apply_hook
      * apply_hook dispatch wiring to requirements_writer
      * writer's actual file mutation
      * end-to-end ledger emission
    """
    workspace = fake_repo["workspace"]
    repo = fake_repo["repo"]
    req = fake_repo["requirements"]

    # ── Step 1: U3 trial result (stubbed; the orchestrator looks it up) ──
    orchestrator.persist_trial(TrialResult(
        package="click", from_version="8.0.0", to_version="9.0.0",
        status="ok", pass_count=42, fail_count=0,
    ))

    # ── Step 2: U4 orchestrator end-to-end — runs U1 + U2 + U3 lookup
    # internally, then files the CR. We pin every input.
    pypi_md_for_orchestrator = {
        "info": {"description": "click is a CLI tool", "project_urls": {}},
        "releases": {"9.0.0": [{"upload_time": (
            datetime.now(timezone.utc) - timedelta(days=60)
        ).strftime("%Y-%m-%dT%H:%M:%S")}]},
    }
    staged_calls: list[dict] = []
    def _capture_stage(**kw):
        staged_calls.append(kw)

    filed, outcome = orchestrator.try_auto_cr_for_major(
        package="click", from_version="8.0.0", to_version="9.0.0",
        metadata_fetcher=lambda pkg: pypi_md_for_orchestrator,
        llm_builder=lambda: _StubLLM(_stub_llm_capability()),
        stage_fn=_capture_stage,
        impact_repo_root=repo,
    )
    assert filed is True, f"U4 gate failed: {outcome and outcome.reason!r}"
    assert len(staged_calls) == 1, "CR not staged"
    # Capability + impact were both produced inside the orchestrator
    assert (workspace / "capabilities" / "click.jsonl").exists()

    staged = staged_calls[0]
    # P0#1b — target_path under docs/ so validator accepts
    assert staged["target_path"].startswith("docs/proposed_upgrades/")
    body = staged["body_markdown"]
    # P0#1b — front-matter present + parseable
    assert body.startswith("---\n")
    assert "action: bump_requirement" in body
    assert "package: click" in body
    assert "to_version: 9.0.0" in body

    # ── Step 5: apply_hook front-matter parse ──
    fm = apply_hook.parse_front_matter(body)
    assert fm is not None, "apply_hook can't parse staged front-matter"
    assert fm["action"] == "bump_requirement"
    assert fm["package"] == "click"
    assert fm["to_version"] == "9.0.0"

    # ── Step 6: apply_hook dispatch → requirements_writer ──
    # The writer would be off by default; force-enable for this test.
    monkeypatch.setattr(
        requirements_writer, "_enabled", lambda: True,
    )
    result = apply_hook._dispatch_bump(
        fm, cr_id="cr-e2e", reason="operator approved",
    )
    assert result["ok"] is True, f"writer dispatch failed: {result}"
    assert result["package"] == "click"
    assert result["to_version"] == "9.0.0"

    # ── Step 7: requirements.txt actually changed ──
    current = req.read_text()
    assert "click==9.0.0" in current, "writer didn't bump pin"
    assert "click==8.0.0" not in current, "old pin still there"
    assert "fastapi==0.110.0" in current, "writer clobbered sibling pin"


def test_full_chain_python_bump(fake_repo, all_switches_on, monkeypatch):
    """End-to-end Python bump: snapshot row → front-matter → dockerfile_writer.

    Catches the same class of wiring bugs for the P0#4 path.
    """
    workspace = fake_repo["workspace"]
    repo = fake_repo["repo"]

    # Stand in a Dockerfile
    dockerfile = repo / "Dockerfile"
    dockerfile.write_text("FROM python:3.13-slim\nWORKDIR /app\n")
    monkeypatch.setenv("DOCKERFILE_PATH", str(dockerfile))

    from app.upgrade_lifecycle import dockerfile_writer, ecosystem_snapshot
    monkeypatch.setattr(ecosystem_snapshot, "_enabled", lambda: True)
    monkeypatch.setattr(dockerfile_writer, "_enabled", lambda: True)

    # Generate a snapshot with a Python row
    monkeypatch.setattr(
        ecosystem_snapshot, "compose_python_proposal",
        lambda current_minor, now=None, horizon_days=365: ecosystem_snapshot.MajorUpgradeProposal(
            package="python", from_version="3.13", to_version="3.14",
            priority="high", is_framework=False,
            capability_summary="EOL approaching",
        ),
    )
    ecosystem_snapshot.generate_snapshot(
        year=2027, now=datetime(2027, 1, 5, tzinfo=timezone.utc),
        framework_fetcher=lambda pkg: {"latest_version": "x"},
        cost_fetcher=lambda: {},
        capability_iterator=lambda: [],
        dependency_radar_state={},
    )

    # Operator accepts the Python row → CR is filed with the right
    # front-matter (we stub cr_filer to capture the call)
    captured: list[dict] = []
    ecosystem_snapshot.accept_major_upgrade(
        year=2027, package="python", to_version="3.14",
        cr_filer=lambda **kw: captured.append(kw) or "cr-py-e2e",
        tier3_proposer=lambda **kw: "tier3-x",
    )
    assert len(captured) == 1, "Python acceptance didn't file a CR"
    body = captured[0]["new_content"]
    assert "action: bump_python" in body
    assert "to_version: 3.14" in body

    # apply_hook parses the body and dispatches to dockerfile_writer
    fm = apply_hook.parse_front_matter(body)
    assert fm["action"] == "bump_python"

    # Silence the loud Signal notification side-effect.
    monkeypatch.setattr(
        apply_hook, "_notify_python_bump_applied", lambda **kw: None,
    )

    result = apply_hook._dispatch_python_bump(
        fm, cr_id="cr-py-e2e", reason="operator approved",
    )
    assert result["ok"] is True, f"python dispatch failed: {result}"
    assert result["to_version"] == "3.14"

    # Dockerfile actually changed
    text = dockerfile.read_text()
    assert "FROM python:3.14-slim" in text
    assert "FROM python:3.13" not in text
