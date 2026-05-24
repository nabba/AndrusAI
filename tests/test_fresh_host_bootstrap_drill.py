"""Gap 1 — fresh_host_bootstrap drill tests.

The drill verifies that a clean machine could become a working
AndrusAI substrate by checking three layers:

  1. Install-path artifacts (install.sh + requirements.txt +
     docker-compose.yml + scripts/install/*.sh)
  2. Latest DR tarball restores cleanly into a scratch directory
  3. Source-ledger hash chains in the restored data are intact

Each layer is tested in isolation here; the integration test
exercises the full run() against happy + sad inputs.
"""
from __future__ import annotations

import stat
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# The drill module imports cleanly, but the sibling task_recovery
# drill (auto-imported by app.resilience_drills.drills.__init__)
# pulls in pydantic via its CrewAI fixture. Skip the whole file on
# hosts without pydantic — matches the existing codebase pattern
# (see tests/test_capability_e2e.py + tests/test_photos_tools.py).
pytest.importorskip("pydantic")


@pytest.fixture(autouse=True)
def isolated_workspace(monkeypatch, tmp_path):
    """Per-test workspace + repo roots so each drill invocation
    sees a clean scratch area."""
    from app import paths as _paths

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(_paths, "WORKSPACE_ROOT", workspace)
    return workspace


def _make_install_path(repo: Path, *, valid: bool = True) -> None:
    """Build the minimum install-path artifacts the drill checks."""
    repo.mkdir(parents=True, exist_ok=True)
    install_sh = repo / "install.sh"
    body = "#!/usr/bin/env bash\nset -euo pipefail\n" + (
        "echo 'install line'\n" * 120
    )
    install_sh.write_text(body)
    if valid:
        install_sh.chmod(install_sh.stat().st_mode | stat.S_IXUSR)

    req = repo / "requirements.txt"
    if valid:
        req.write_text(
            "\n".join(
                f"package_{i}==1.{i}.0" for i in range(30)
            )
        )
    else:
        req.write_text("only_one_pin==1.0\n")

    compose = repo / "docker-compose.yml"
    compose.write_text(
        textwrap.dedent(
            """\
            services:
              gateway:
                image: andrusai:latest
              postgres:
                image: postgres:15
            """
        )
    )

    install_dir = repo / "scripts" / "install"
    install_dir.mkdir(parents=True, exist_ok=True)
    for name in ("lib.sh", "local.sh", "prereqs.sh", "verify.sh"):
        (install_dir / name).write_text("# stub\n")


def test_check_install_path_happy(monkeypatch, tmp_path):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    repo = tmp_path / "repo"
    _make_install_path(repo, valid=True)
    monkeypatch.setattr(drill, "_check_docker_compose", lambda r: {"ok": True, "method": "yaml"})
    out = drill._check_install_path(repo)
    assert out["status"] == "pass"
    assert out["install_sh_executable"] is True
    assert out["install_sh_lines"] > drill._INSTALL_SH_MIN_LINES
    assert out["requirements_lines"] >= drill._REQUIREMENTS_MIN_LINES


def test_check_install_path_fails_when_install_sh_too_short(monkeypatch, tmp_path):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    repo = tmp_path / "repo"
    _make_install_path(repo, valid=True)
    (repo / "install.sh").write_text("#!/usr/bin/env bash\necho oops\n")
    monkeypatch.setattr(drill, "_check_docker_compose", lambda r: {"ok": True})
    out = drill._check_install_path(repo)
    assert out["status"] == "fail"
    assert "suspiciously short" in out["reason"]


def test_check_install_path_fails_when_requirements_unpinned(monkeypatch, tmp_path):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    repo = tmp_path / "repo"
    _make_install_path(repo, valid=True)
    (repo / "requirements.txt").write_text(
        "\n".join(f"package_{i}" for i in range(40))
    )
    monkeypatch.setattr(drill, "_check_docker_compose", lambda r: {"ok": True})
    out = drill._check_install_path(repo)
    assert out["status"] == "fail"
    assert "no version pins" in out["reason"]


def test_check_install_path_fails_when_required_file_missing(monkeypatch, tmp_path):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    repo = tmp_path / "repo"
    _make_install_path(repo, valid=True)
    (repo / "scripts/install/lib.sh").unlink()
    out = drill._check_install_path(repo)
    assert out["status"] == "fail"
    assert "scripts/install/lib.sh" in out["missing_files"]


def test_check_docker_compose_yaml_round_trip(tmp_path):
    """When the docker CLI is unavailable, fall back to YAML parse."""
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text(
        "services:\n  gateway:\n    image: andrusai:latest\n"
    )
    out = drill._check_docker_compose(repo)
    assert out["ok"] is True
    assert out["method"] in ("docker_compose_config", "yaml")


def test_check_docker_compose_fails_on_garbage(tmp_path):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yml").write_text(":\n  this is not yaml\n  - {{\n")
    out = drill._check_docker_compose(repo)
    assert out["ok"] is False


def test_check_minimum_workspace_happy(isolated_workspace):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    scratch = isolated_workspace / "scratch"
    for rel in drill._MINIMUM_WORKSPACE_FILE_SET:
        full = scratch / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("{}\n")
    out = drill._check_minimum_workspace(scratch)
    assert out["ok"] is True
    assert out["missing"] == []


def test_check_minimum_workspace_reports_missing(isolated_workspace):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    scratch = isolated_workspace / "scratch"
    scratch.mkdir()
    out = drill._check_minimum_workspace(scratch)
    assert out["ok"] is False
    assert set(out["missing"]) == set(drill._MINIMUM_WORKSPACE_FILE_SET)


def test_check_source_ledgers_no_chromadb_root_treated_as_ok(isolated_workspace):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    scratch = isolated_workspace / "scratch"
    scratch.mkdir()
    out = drill._check_source_ledgers(scratch)
    assert out["ok"] is True
    assert out["kb_results"] == []


def test_check_source_ledgers_walks_each_kb(monkeypatch, isolated_workspace):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    scratch = isolated_workspace / "scratch"
    (scratch / "chromadb" / "kb_a").mkdir(parents=True)
    (scratch / "chromadb" / "kb_a" / ".source_ledger.jsonl").write_text("{}\n")
    (scratch / "chromadb" / "kb_b").mkdir(parents=True)
    (scratch / "chromadb" / "kb_b" / ".source_ledger.jsonl").write_text("{}\n")
    fake_chain = MagicMock(ok=True, first_bad_row=None, first_bad_reason=None)
    monkeypatch.setattr(
        "app.memory.source_ledger.verify_chain", lambda kb, ledger_path=None: fake_chain
    )
    out = drill._check_source_ledgers(scratch)
    assert out["ok"] is True
    assert {row["kb"] for row in out["kb_results"]} == {"kb_a", "kb_b"}


def test_check_source_ledgers_propagates_chain_break(monkeypatch, isolated_workspace):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    scratch = isolated_workspace / "scratch"
    (scratch / "chromadb" / "kb_a").mkdir(parents=True)
    (scratch / "chromadb" / "kb_a" / ".source_ledger.jsonl").write_text("{}\n")
    bad = MagicMock(ok=False, first_bad_row=42, first_bad_reason="prev_hash mismatch")
    monkeypatch.setattr(
        "app.memory.source_ledger.verify_chain", lambda kb, ledger_path=None: bad
    )
    out = drill._check_source_ledgers(scratch)
    assert out["ok"] is False


def test_run_fails_when_install_path_broken(monkeypatch, tmp_path):
    """When the install path is broken, the drill MUST short-circuit
    before attempting a restore. The restore is expensive and the
    fix is a different code path entirely."""
    from app.resilience_drills.drills import fresh_host_bootstrap as drill
    from app.resilience_drills.protocol import DrillStatus

    repo = tmp_path / "broken_repo"
    repo.mkdir()
    (repo / "install.sh").write_text("# nothing\n")
    monkeypatch.setattr(drill, "_repo_root", lambda: repo)
    restore_called = [False]

    def _no_restore(*args, **kwargs):
        restore_called[0] = True
        return {"ok": True}

    monkeypatch.setattr(drill, "_restore_to_scratch", _no_restore)
    result = drill.run(dry_run=True)
    assert result.status == DrillStatus.FAIL
    assert restore_called[0] is False  # short-circuited
    assert any("install path broken" in err for err in result.errors)


def test_run_passes_when_every_layer_ok(monkeypatch, tmp_path):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill
    from app.resilience_drills.protocol import DrillStatus

    repo = tmp_path / "happy_repo"
    _make_install_path(repo, valid=True)
    monkeypatch.setattr(drill, "_repo_root", lambda: repo)
    monkeypatch.setattr(drill, "_check_docker_compose", lambda r: {"ok": True})
    monkeypatch.setattr(
        drill,
        "_restore_to_scratch",
        lambda scratch: {"ok": True, "tarball": "/x.tar.gz"},
    )

    def _populate_scratch(scratch):
        for rel in drill._MINIMUM_WORKSPACE_FILE_SET:
            full = Path(scratch) / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("{}\n")
        return {"ok": True, "tarball": "/x.tar.gz"}

    monkeypatch.setattr(drill, "_restore_to_scratch", _populate_scratch)
    monkeypatch.setattr(drill, "_check_source_ledgers", lambda s: {"ok": True, "kb_results": []})
    monkeypatch.setattr(drill, "_dockerized_smoke", lambda s, r: {"skipped": True})
    result = drill.run(dry_run=True)
    assert result.status == DrillStatus.PASS
    assert result.observation["install_path_ok"] is True
    assert result.observation["restore_ok"] is True
    assert result.observation["source_ledgers_ok"] is True


def test_run_fails_when_restore_fails(monkeypatch, tmp_path):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill
    from app.resilience_drills.protocol import DrillStatus

    repo = tmp_path / "repo"
    _make_install_path(repo, valid=True)
    monkeypatch.setattr(drill, "_repo_root", lambda: repo)
    monkeypatch.setattr(drill, "_check_docker_compose", lambda r: {"ok": True})
    monkeypatch.setattr(
        drill,
        "_restore_to_scratch",
        lambda s: {"ok": False, "reason": "no tarball available", "errors": []},
    )
    result = drill.run(dry_run=True)
    assert result.status == DrillStatus.FAIL
    assert any("no tarball available" in err for err in result.errors)


def test_run_records_dockerized_skipped_when_switch_off(monkeypatch, tmp_path):
    from app.resilience_drills.drills import fresh_host_bootstrap as drill
    from app.resilience_drills.protocol import DrillStatus

    repo = tmp_path / "repo"
    _make_install_path(repo, valid=True)
    monkeypatch.setattr(drill, "_repo_root", lambda: repo)
    monkeypatch.setattr(drill, "_check_docker_compose", lambda r: {"ok": True})

    def _populate_scratch(scratch):
        for rel in drill._MINIMUM_WORKSPACE_FILE_SET:
            full = Path(scratch) / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("{}\n")
        return {"ok": True, "tarball": "/x.tar.gz"}

    monkeypatch.setattr(drill, "_restore_to_scratch", _populate_scratch)
    monkeypatch.setattr(drill, "_check_source_ledgers", lambda s: {"ok": True, "kb_results": []})

    from app import runtime_settings

    monkeypatch.setattr(
        runtime_settings, "get_drill_fresh_host_bootstrap_dockerized_enabled", lambda: False
    )
    result = drill.run(dry_run=True)
    assert result.status == DrillStatus.PASS
    assert result.detail["dockerized"].get("skipped") is True


def test_spec_registered_with_correct_master_switch():
    """The drill must declare its master switch so the Q18
    orchestrator gate can find it."""
    from app.resilience_drills.drills import fresh_host_bootstrap as drill
    from app.resilience_drills.protocol import DrillRisk

    assert drill.SPEC.name == "fresh_host_bootstrap"
    assert drill.SPEC.risk == DrillRisk.LOW
    assert drill.SPEC.requires_master_switch == "drill_fresh_host_bootstrap_enabled"
    assert drill.SPEC.cadence_days == 90
    assert drill.SPEC.warmup_days == 14


def test_spec_in_registry():
    """Importing app.resilience_drills.drills must register the
    10th drill into the global registry."""
    from app.resilience_drills.drills import fresh_host_bootstrap  # noqa: F401
    from app.resilience_drills.protocol import _registry

    assert "fresh_host_bootstrap" in _registry.list_names()


def test_runtime_settings_master_switch_default_on():
    """Master switch defaults to ON (drill is LOW-risk + read-only)."""
    from app import runtime_settings

    assert runtime_settings.get_drill_fresh_host_bootstrap_enabled() is True


def test_runtime_settings_dockerized_switch_default_off():
    """The dockerized companion switch defaults to OFF because it
    requires Docker daemon access from the gateway."""
    from app import runtime_settings

    assert runtime_settings.get_drill_fresh_host_bootstrap_dockerized_enabled() is False


def test_scripts_bootstrap_fresh_host_exists_and_executable():
    """The operator-callable script must exist and be executable."""
    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "bootstrap_fresh_host.sh"
    assert script.exists(), f"missing: {script}"
    st = script.stat()
    assert bool(st.st_mode & stat.S_IXUSR), "bootstrap_fresh_host.sh not executable"


def test_minimum_file_sets_have_no_overlap_with_secret_paths():
    """The minimum file set MUST NOT include any secret-shaped path.

    The DR exporter aggressively denylists secrets (.env, credentials,
    tokens, etc.); the drill's expectations must compose with that —
    a fresh host should bootstrap WITHOUT any secret material in the
    restored workspace because the operator brings secrets via env or
    External Secrets at first boot.
    """
    from app.resilience_drills.drills import fresh_host_bootstrap as drill

    blocked = ("token", "credential", "private_key", ".env", "secret")
    for rel in drill._MINIMUM_WORKSPACE_FILE_SET:
        lower = rel.lower()
        for sub in blocked:
            assert sub not in lower, (
                f"minimum file set references secret-shaped path: {rel}"
            )
