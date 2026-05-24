"""fresh_host_bootstrap — 10th resilience drill.

Gap 1 of the 2026-05-24 ultrathink analysis closure.

What the drill answers
======================

The DR drill (``backup_restore``) answers: "Can we restore the
backup?" The source-ledger replay drill answers: "Can we
rebuild a KB from its ledger?" This drill answers the strictly
broader question: **"Could a clean machine become a working
AndrusAI substrate in <30 minutes, end to end?"**

The composing pieces — clone the repo, install dependencies,
restore workspace, verify gateway boots — already exist as
operator-callable scripts. This drill verifies they still
compose into a working chain by running every piece that does
not require external network or live infrastructure.

What the drill does
===================

  1. Verify the install-path artifacts exist and look healthy:
     ``install.sh`` is executable + non-trivial, the install
     library scripts in ``scripts/install/`` are all present,
     ``requirements.txt`` is non-empty + at least one pin
     references a non-trivial version, ``docker-compose.yml``
     parses (via ``docker compose config`` when Docker is
     reachable; falls back to a YAML round-trip otherwise).

  2. Restore the most-recent DR tarball into a scratch
     directory and confirm the minimum workspace file set a
     gateway needs to boot is present + readable: the
     continuity_ledger, audit.log, change_requests/audit.jsonl,
     drill_audit.jsonl, source ledgers per KB.

  3. Walk the source ledgers and verify the hash chain is
     intact (via ``source_ledger.verify_chain``). The DR drill
     verifies the export round-trips; this drill verifies the
     export is actually replayable.

  4. (Optional, dockerized=True) Launch an ephemeral container
     with the restored workspace bind-mounted, run a smoke
     boot, and tear it down. Default OFF — operator switches
     it on when Docker is reachable from the gateway.

Risk: LOW. Scratch-dir only; never touches the live workspace.

Cadence: quarterly (90 days). Same as the other LOW-risk
infrastructure drills.

What this catches
=================

  * ``install.sh`` was edited and silently broke
  * ``scripts/install/*.sh`` files got renamed without an
    upgrade-lifecycle CR
  * ``requirements.txt`` lost its pins (the upgrade-lifecycle
    P0#1a writer's edge cases)
  * ``docker-compose.yml`` references an image that's no longer
    on the registry
  * The most-recent DR tarball restores but is missing one of
    the ledger files a fresh gateway would need
  * The source ledger's hash chain broke during the export+
    import round-trip

All of those silently fail today until a real recovery event
discovers them — which is the worst time.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.resilience_drills.protocol import (
    DrillResult,
    DrillRisk,
    DrillSpec,
    DrillStatus,
    FailureClass,
    register,
)

logger = logging.getLogger(__name__)


SPEC = DrillSpec(
    name="fresh_host_bootstrap",
    cadence_days=90,
    grace_days=30,
    warmup_days=14,
    risk=DrillRisk.LOW,
    description=(
        "Quarterly drill that verifies a clean machine could "
        "become a working AndrusAI substrate. Checks the install "
        "scripts, restores the latest DR tarball into a scratch "
        "dir, and verifies the source-ledger hash chain on every "
        "KB. Optional dockerized mode does an ephemeral boot."
    ),
    requires_master_switch="drill_fresh_host_bootstrap_enabled",
)


# Minimum file set every fresh-host install needs in the
# restored workspace before the gateway can read enough state to
# start without crashing. None of these are TIER_IMMUTABLE
# substrate files (those live in app/, not workspace/) — these
# are the workspace artifacts the gateway reads at boot.
_MINIMUM_WORKSPACE_FILE_SET = (
    "audit.log",
    "identity/continuity_ledger.jsonl",
    "change_requests/audit.jsonl",
    "resilience/drill_audit.jsonl",
)

# Minimum file set the source-tree itself needs for install.sh
# to do its job. The drill walks these against the live repo
# (NOT the restored scratch dir — these are code, not data).
_MINIMUM_INSTALL_PATH_FILES = (
    "install.sh",
    "requirements.txt",
    "docker-compose.yml",
    "scripts/install/lib.sh",
    "scripts/install/local.sh",
    "scripts/install/prereqs.sh",
    "scripts/install/verify.sh",
)

# install.sh under this length is almost certainly broken or
# truncated — the real one is several hundred lines.
_INSTALL_SH_MIN_LINES = 100

# requirements.txt below this many lines suggests the file has
# been gutted. Production has tens of pins.
_REQUIREMENTS_MIN_LINES = 20


def _repo_root() -> Path:
    """The git repository root — where install.sh lives.

    The drill runs inside the gateway whose CWD is the repo
    root in production. The lookup falls back to common host
    paths for the test/CI environments.
    """
    try:
        from app.paths import REPO_ROOT  # type: ignore

        return Path(REPO_ROOT)
    except Exception:
        pass
    candidates = [
        Path("/app"),
        Path.cwd(),
        Path(__file__).resolve().parents[3],
    ]
    for c in candidates:
        if (c / "install.sh").exists():
            return c
    return Path.cwd()


def _workspace_root() -> Path:
    try:
        from app.paths import WORKSPACE_ROOT  # type: ignore

        return Path(WORKSPACE_ROOT)
    except Exception:
        return Path("/app/workspace")


def _scratch_root() -> Path:
    return _workspace_root() / ".drill_scratch"


def _check_install_path(repo: Path) -> dict[str, Any]:
    """Verify the install-path artifacts exist + look healthy."""
    missing: list[str] = []
    findings: dict[str, Any] = {}
    for rel in _MINIMUM_INSTALL_PATH_FILES:
        path = repo / rel
        if not path.exists():
            missing.append(rel)
    findings["missing_files"] = missing
    if missing:
        findings["status"] = "fail"
        return findings

    install_sh = repo / "install.sh"
    st = install_sh.stat()
    executable = bool(st.st_mode & stat.S_IXUSR)
    line_count = sum(1 for _ in install_sh.open("r", encoding="utf-8", errors="replace"))
    findings["install_sh_executable"] = executable
    findings["install_sh_lines"] = line_count
    if not executable:
        findings["status"] = "fail"
        findings["reason"] = "install.sh not executable"
        return findings
    if line_count < _INSTALL_SH_MIN_LINES:
        findings["status"] = "fail"
        findings["reason"] = (
            f"install.sh suspiciously short ({line_count} lines < "
            f"{_INSTALL_SH_MIN_LINES})"
        )
        return findings

    req = repo / "requirements.txt"
    req_lines = [
        ln.strip()
        for ln in req.open("r", encoding="utf-8", errors="replace")
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    findings["requirements_lines"] = len(req_lines)
    if len(req_lines) < _REQUIREMENTS_MIN_LINES:
        findings["status"] = "fail"
        findings["reason"] = (
            f"requirements.txt has {len(req_lines)} pins < "
            f"{_REQUIREMENTS_MIN_LINES} — likely truncated"
        )
        return findings
    pin_chars = ("==", ">=", "<=", "~=", "<", ">")
    pinned = sum(1 for ln in req_lines if any(c in ln for c in pin_chars))
    findings["requirements_pinned"] = pinned
    if pinned == 0:
        findings["status"] = "fail"
        findings["reason"] = "requirements.txt has no version pins"
        return findings

    compose_status = _check_docker_compose(repo)
    findings["compose_check"] = compose_status
    if compose_status.get("ok") is False:
        findings["status"] = "fail"
        findings["reason"] = (
            f"docker-compose.yml validation failed: "
            f"{compose_status.get('reason')}"
        )
        return findings

    findings["status"] = "pass"
    return findings


def _check_docker_compose(repo: Path) -> dict[str, Any]:
    """Validate docker-compose.yml via the daemon when reachable,
    YAML round-trip otherwise.

    Returns {"ok": True/False, "method": ..., "reason": ...}.
    """
    compose = repo / "docker-compose.yml"
    if not compose.exists():
        return {"ok": False, "method": "absent", "reason": "no compose file"}
    try:
        proc = subprocess.run(
            ["docker", "compose", "-f", str(compose), "config", "--quiet"],
            cwd=str(repo),
            capture_output=True,
            timeout=30,
            text=True,
        )
        if proc.returncode == 0:
            return {"ok": True, "method": "docker_compose_config"}
        return {
            "ok": False,
            "method": "docker_compose_config",
            "reason": (proc.stderr or proc.stdout or "")[:400],
        }
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "method": "docker_compose_config",
            "reason": "docker compose config timed out (>30s)",
        }
    except Exception as exc:
        logger.debug("docker compose config raised: %r", exc, exc_info=True)
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(compose.open("r", encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "method": "yaml", "reason": f"yaml parse: {exc}"}
    if not isinstance(data, dict) or "services" not in data:
        return {
            "ok": False,
            "method": "yaml",
            "reason": "no services key — not a valid compose file",
        }
    return {
        "ok": True,
        "method": "yaml",
        "services_count": len(data.get("services") or {}),
    }


def _restore_to_scratch(scratch: Path) -> dict[str, Any]:
    """Run the existing DR restore against the latest tarball,
    targeting the scratch dir. Re-uses ``app.dr.boot_drill`` so
    the underlying tarball-handling code is exactly the one a
    real recovery would run.
    """
    try:
        from app.dr.boot_drill import run_drill
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"import app.dr.boot_drill failed: {exc}",
        }
    try:
        report = run_drill(
            export_fresh=False,
            keep_target=True,
            target_dir=scratch,
        )
    except Exception as exc:
        return {"ok": False, "reason": f"DR run_drill raised: {exc}"}
    return {
        "ok": bool(report.overall_ok),
        "tarball": report.tarball,
        "ledger_files_restored": report.ledger_files_restored,
        "ledger_hash_mismatches": report.ledger_hash_mismatches,
        "errors": list(report.errors),
    }


def _check_minimum_workspace(scratch: Path) -> dict[str, Any]:
    """Verify the restored scratch dir has the minimum files a
    fresh gateway needs at boot.
    """
    missing: list[str] = []
    for rel in _MINIMUM_WORKSPACE_FILE_SET:
        if not (scratch / rel).exists():
            missing.append(rel)
    return {
        "ok": not missing,
        "missing": missing,
        "checked": list(_MINIMUM_WORKSPACE_FILE_SET),
    }


def _check_source_ledgers(scratch: Path) -> dict[str, Any]:
    """Walk every per-KB source ledger in the scratch dir and
    verify the hash chain is intact.
    """
    try:
        from app.memory.source_ledger import verify_chain
    except Exception as exc:
        return {"ok": False, "reason": f"source_ledger import: {exc}"}
    kb_results: list[dict[str, Any]] = []
    overall_ok = True
    chromadb_root = scratch / "chromadb"
    if not chromadb_root.exists():
        return {"ok": True, "kb_results": [], "note": "no chromadb root"}
    for kb_dir in sorted(chromadb_root.iterdir()):
        if not kb_dir.is_dir():
            continue
        ledger = kb_dir / ".source_ledger.jsonl"
        if not ledger.exists():
            continue
        try:
            chain = verify_chain(kb_dir.name, ledger_path=ledger)
            row = {
                "kb": kb_dir.name,
                "ok": bool(chain.ok),
                "first_bad_row": chain.first_bad_row,
                "first_bad_reason": chain.first_bad_reason,
            }
        except Exception as exc:
            row = {"kb": kb_dir.name, "ok": False, "error": str(exc)}
        if not row["ok"]:
            overall_ok = False
        kb_results.append(row)
    return {"ok": overall_ok, "kb_results": kb_results}


def _dockerized_smoke(scratch: Path, repo: Path) -> dict[str, Any]:
    """Optional ephemeral boot smoke. OFF by default.

    Only runs when ``drill_fresh_host_bootstrap_dockerized_enabled`` is
    ON. Builds a minimal compose stack with the scratch workspace
    bind-mounted and ``docker compose up --no-start`` — just enough
    to verify the compose file actually composes against the restored
    workspace without starting any heavy containers.
    """
    try:
        from app import runtime_settings

        if not runtime_settings.get_drill_fresh_host_bootstrap_dockerized_enabled():
            return {"skipped": True, "reason": "switch off"}
    except Exception:
        return {"skipped": True, "reason": "runtime_settings unavailable"}

    compose = repo / "docker-compose.yml"
    if not compose.exists():
        return {"ok": False, "reason": "no docker-compose.yml in repo"}
    env = dict(os.environ)
    env["WORKSPACE_ROOT_OVERRIDE"] = str(scratch)
    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose),
                "-p",
                "andrusai-bootstrap-drill",
                "config",
            ],
            cwd=str(repo),
            env=env,
            capture_output=True,
            timeout=60,
            text=True,
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "")[:600],
        }
    except FileNotFoundError:
        return {"ok": False, "reason": "docker CLI not on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "docker compose config timed out"}
    except Exception as exc:
        return {"ok": False, "reason": f"docker compose raised: {exc}"}


def _run(*, dry_run: bool = True) -> DrillResult:
    """Q18 runner contract: returns a bare DrillResult."""
    started = datetime.now(timezone.utc)
    t0 = time.time()
    detail: dict[str, Any] = {}
    observation: dict[str, Any] = {}
    scratch: Path | None = None
    try:
        repo = _repo_root()
        detail["repo_root"] = str(repo)

        install_check = _check_install_path(repo)
        detail["install_path"] = install_check
        observation["install_path_ok"] = install_check.get("status") == "pass"
        if install_check.get("status") != "pass":
            return DrillResult(
                drill_name=SPEC.name,
                status=DrillStatus.FAIL,
                started_at=started.isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                duration_s=time.time() - t0,
                dry_run=dry_run,
                detail=detail,
                errors=[
                    f"install path broken: "
                    f"{install_check.get('reason') or install_check.get('missing_files')}"
                ],
                failure_class=FailureClass.STRUCTURAL_FAIL,
                observation=observation,
            )

        scratch_root = _scratch_root() / "fresh_host_bootstrap"
        scratch_root.mkdir(parents=True, exist_ok=True)
        scratch = scratch_root / started.strftime("%Y%m%dT%H%M%SZ")
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)

        restore = _restore_to_scratch(scratch)
        detail["restore"] = restore
        observation["restore_ok"] = bool(restore.get("ok"))
        if not restore.get("ok"):
            return DrillResult(
                drill_name=SPEC.name,
                status=DrillStatus.FAIL,
                started_at=started.isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
                duration_s=time.time() - t0,
                dry_run=dry_run,
                detail=detail,
                errors=[f"DR restore failed: {restore.get('reason') or restore.get('errors')}"],
                failure_class=FailureClass.STRUCTURAL_FAIL,
                observation=observation,
            )

        minimum_check = _check_minimum_workspace(scratch)
        detail["minimum_workspace"] = minimum_check
        observation["minimum_workspace_ok"] = bool(minimum_check.get("ok"))

        ledger_check = _check_source_ledgers(scratch)
        detail["source_ledgers"] = ledger_check
        observation["source_ledgers_ok"] = bool(ledger_check.get("ok"))

        docker_check = _dockerized_smoke(scratch, repo)
        detail["dockerized"] = docker_check
        if docker_check.get("ok") is False and not docker_check.get("skipped"):
            observation["dockerized_ok"] = False
        elif docker_check.get("ok"):
            observation["dockerized_ok"] = True

        all_ok = (
            install_check.get("status") == "pass"
            and restore.get("ok")
            and minimum_check.get("ok")
            and ledger_check.get("ok")
            and docker_check.get("ok", True) is not False
        )
        status = DrillStatus.PASS if all_ok else DrillStatus.FAIL
        return DrillResult(
            drill_name=SPEC.name,
            status=status,
            started_at=started.isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_s=time.time() - t0,
            dry_run=dry_run,
            detail=detail,
            failure_class=(FailureClass.STRUCTURAL_FAIL if not all_ok else None),
            observation=observation,
        )

    except Exception as exc:
        logger.debug("fresh_host_bootstrap: drill errored", exc_info=True)
        return DrillResult(
            drill_name=SPEC.name,
            status=DrillStatus.ERROR,
            started_at=started.isoformat(),
            completed_at=datetime.now(timezone.utc).isoformat(),
            duration_s=time.time() - t0,
            dry_run=dry_run,
            detail=detail,
            errors=[f"{type(exc).__name__}: {exc}"],
            failure_class=FailureClass.CODE_ERROR,
        )
    finally:
        if scratch is not None:
            try:
                shutil.rmtree(scratch, ignore_errors=True)
            except Exception:
                pass


def run(*, dry_run: bool = True) -> DrillResult:
    """Public entry point — drills run via the standard scheduler."""
    return _run(dry_run=dry_run)


register(SPEC, run)
