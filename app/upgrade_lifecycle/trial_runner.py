"""U3 — Upgrade trial harness.

PROGRAM §62 — Stage D of the upgrade lifecycle. Bumps the requirement
line for one package in a throwaway temp directory, pip-installs the
new version, and runs the test suite under a wallclock cap. Outputs a
:class:`~app.upgrade_lifecycle.protocol.TrialResult` that U4's MAJOR
auto-CR gate consults: passing trial is one of five gate conditions.

Design choices:

  * **Temp directory, not coding-session worktree** — the trial is an
    internal subsystem job, not an agent action. It needs no quota
    accounting, no submit step, no operator visibility per-session.
    A tempdir + ``shutil.copy*`` is two orders of magnitude faster
    than spinning a real worktree.
  * **Subprocess primitives injected** — tests stub out
    pip + pytest so they can exercise every branch without actually
    running the heavy commands.
  * **Wallclock cap** — pytest runs with a hard wallclock cap
    (default 10 min). On timeout we collect whatever it produced and
    flag the result as ``timeout``.
  * **Failure-isolated** — any infrastructure error (tempdir create
    fails, pip not present, pytest binary missing) yields
    ``TrialResult(status="infrastructure_error", ...)`` rather than
    raising. The daemon loop never sees an exception.

Trial cost is dominated by pip-install; LLM cost is zero (this stage
is purely mechanical). The ``cost_estimate_usd`` field is included on
the result for future use when sandbox-execution-as-a-service tooling
becomes a separate budget category.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from app.upgrade_lifecycle.protocol import TrialResult

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


_DEFAULT_WALLCLOCK_S = 600          # 10 min hard cap on pytest
_DEFAULT_INSTALL_TIMEOUT_S = 600     # 10 min hard cap on pip install
_MAX_FAILURE_BLURBS = 5
_PYTEST_FAIL_LINE_RE = re.compile(
    r"^(FAILED|ERROR)\s+(?P<test>[\w./:\[\]\-]+)",
    re.MULTILINE,
)
_PYTEST_SUMMARY_RE = re.compile(
    r"(?P<passed>\d+)\s+passed"
    r"(?:.*?(?P<failed>\d+)\s+failed)?"
    r"(?:.*?(?P<errors>\d+)\s+error[s]?)?",
)


# ── Subprocess shims (injectable for tests) ──────────────────────────────


PipInstaller = Callable[[Path, str, str, int], tuple[int, str, str]]
PytestRunner = Callable[[Path, int], tuple[int, str, str]]


def _default_pip_install(
    cwd: Path, package: str, version: str, timeout_s: int,
) -> tuple[int, str, str]:
    """``pip install <pkg>==<ver>`` in *cwd*. Returns ``(rc, stdout, stderr)``."""
    try:
        result = subprocess.run(
            ["pip", "install", "--quiet", f"{package}=={version}"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "pip install timed out"
    except FileNotFoundError:
        return 127, "", "pip binary not found"
    except Exception as exc:
        return 1, "", f"pip exec error: {exc}"


def _default_pytest(cwd: Path, timeout_s: int) -> tuple[int, str, str]:
    """``pytest tests/ -q --tb=no``. Returns ``(rc, stdout, stderr)``."""
    try:
        result = subprocess.run(
            ["pytest", "tests/", "-q", "--tb=no", f"--timeout={timeout_s - 5}"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "pytest timed out"
    except FileNotFoundError:
        return 127, "", "pytest binary not found"
    except Exception as exc:
        return 1, "", f"pytest exec error: {exc}"


# ── Workspace setup ──────────────────────────────────────────────────────


def _bump_requirement(requirements_text: str, package: str, version: str) -> str:
    """Update *requirements_text* to pin ``package==version``.

    Matches the requirements-file format permissively: handles
    `name==X.Y.Z`, `name>=X`, `name~=X.Y`, comments / blank lines, and
    case-insensitive package names with hyphen/underscore variations.

    If the package isn't present in the file, appends a new pin line —
    extras like a transitively-required package can still be tested.
    """
    norm_target = package.lower().replace("_", "-")
    out_lines: list[str] = []
    found = False
    line_re = re.compile(
        r"^(?P<prefix>[\s]*)(?P<name>[A-Za-z0-9_.-]+)"
        r"(?P<sep>\s*(?:==|>=|<=|~=|>|<|!=)\s*)",
    )
    for raw in requirements_text.splitlines():
        m = line_re.match(raw)
        if m:
            name = m.group("name").lower().replace("_", "-")
            if name == norm_target:
                out_lines.append(f"{m.group('prefix')}{m.group('name')}=={version}")
                found = True
                continue
        out_lines.append(raw)
    if not found:
        out_lines.append(f"{package}=={version}")
    return "\n".join(out_lines) + ("\n" if not requirements_text.endswith("\n") else "")


def _enabled() -> bool:
    try:
        from app.runtime_settings import get_upgrade_lifecycle_trial_enabled
        return get_upgrade_lifecycle_trial_enabled()
    except Exception:
        return True


# ── Pytest output parsing ────────────────────────────────────────────────


def _parse_pytest_output(stdout: str, stderr: str) -> tuple[int, int, tuple[str, ...]]:
    """Best-effort extract ``(pass_count, fail_count, failure_blurbs)``.

    pytest's `-q --tb=no` output ends with a one-liner of the form::

        13 passed, 2 failed, 1 error in 3.45s

    plus per-failure FAILED/ERROR lines earlier in the stream.
    """
    combined = (stdout or "") + "\n" + (stderr or "")
    pass_count = 0
    fail_count = 0
    failures: list[str] = []

    # Failure lines first — these are reliable even on truncated output.
    for m in _PYTEST_FAIL_LINE_RE.finditer(combined):
        if len(failures) < _MAX_FAILURE_BLURBS:
            failures.append(m.group("test"))

    # Summary line — walk lines in reverse to find the LAST summary
    # (pytest may print intermediate summaries on rerun).
    for line in reversed(combined.splitlines()):
        if "passed" in line or "failed" in line:
            m = _PYTEST_SUMMARY_RE.search(line)
            if m:
                pass_count = int(m.group("passed") or 0)
                fail_count = (
                    int(m.group("failed") or 0)
                    + int(m.group("errors") or 0)
                )
                break

    # If no summary parsed and we have explicit FAILED entries, count them.
    if pass_count == 0 and fail_count == 0 and failures:
        fail_count = len(failures)

    return pass_count, fail_count, tuple(failures)


# ── Public API ───────────────────────────────────────────────────────────


def run_trial(
    *,
    package: str,
    from_version: str,
    to_version: str,
    repo_root: Path,
    pip_installer: Optional[PipInstaller] = None,
    pytest_runner: Optional[PytestRunner] = None,
    wallclock_s: int = _DEFAULT_WALLCLOCK_S,
    install_timeout_s: int = _DEFAULT_INSTALL_TIMEOUT_S,
) -> TrialResult:
    """Trial-run an upgrade for *package* in a throwaway temp directory.

    Steps:

      1. Make a tempdir, copy ``app/``, ``tests/``, ``conftest.py``,
         and ``requirements.txt`` into it.
      2. Bump the requirement to ``package==to_version``.
      3. ``pip install -r requirements.txt`` (or just the one package
         — we install only the bumped package; the rest of the venv
         is shared with the parent process).
      4. ``pytest tests/`` with the supplied wallclock cap.
      5. Parse output, build TrialResult.

    Failure-isolated: any infrastructure error returns
    ``TrialResult(status="infrastructure_error", ...)`` without raising.
    """
    if not _enabled():
        return TrialResult(
            package=package, from_version=from_version, to_version=to_version,
            status="infrastructure_error",
            failures=("trial subsystem disabled",),
        )

    started = time.time()
    pip_run = pip_installer or _default_pip_install
    pytest_run = pytest_runner or _default_pytest

    tmpdir: Optional[str] = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="ul_trial_")
        sandbox = Path(tmpdir)
        _materialize_sandbox(sandbox, repo_root, package, to_version)

        # Install ONLY the bumped package — sharing the rest of the parent
        # venv keeps the trial fast and rules out unrelated install issues.
        install_rc, install_out, install_err = pip_run(
            sandbox, package, to_version, install_timeout_s,
        )
        if install_rc == 124:
            return TrialResult(
                package=package, from_version=from_version, to_version=to_version,
                status="timeout",
                failures=("pip install timed out",),
                elapsed_s=time.time() - started,
            )
        if install_rc != 0:
            return TrialResult(
                package=package, from_version=from_version, to_version=to_version,
                status="install_failure",
                failures=tuple((install_err or install_out or "")
                              .splitlines()[:_MAX_FAILURE_BLURBS]),
                elapsed_s=time.time() - started,
            )

        # Run pytest.
        pytest_rc, pytest_out, pytest_err = pytest_run(sandbox, wallclock_s)
        elapsed = time.time() - started
        pass_count, fail_count, failures = _parse_pytest_output(pytest_out, pytest_err)

        if pytest_rc == 124:
            return TrialResult(
                package=package, from_version=from_version, to_version=to_version,
                status="timeout",
                pass_count=pass_count, fail_count=fail_count,
                failures=failures,
                elapsed_s=elapsed,
            )
        if pytest_rc == 127:
            return TrialResult(
                package=package, from_version=from_version, to_version=to_version,
                status="infrastructure_error",
                failures=("pytest binary not found",),
                elapsed_s=elapsed,
            )

        # rc == 0 → all green. rc == 1 → some failures (still parseable).
        if pytest_rc == 0 and fail_count == 0:
            status = "ok"
        elif fail_count > 0:
            status = "test_failure"
        else:
            # Non-zero rc but no failures parsed — treat as test_failure.
            status = "test_failure"

        return TrialResult(
            package=package, from_version=from_version, to_version=to_version,
            status=status,
            pass_count=pass_count, fail_count=fail_count,
            failures=failures,
            elapsed_s=elapsed,
        )

    except Exception as exc:
        logger.debug("ul.trial: infrastructure error", exc_info=True)
        return TrialResult(
            package=package, from_version=from_version, to_version=to_version,
            status="infrastructure_error",
            failures=(str(exc)[:200],),
            elapsed_s=time.time() - started,
        )
    finally:
        if tmpdir and os.path.exists(tmpdir):
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                logger.debug("ul.trial: cleanup failed", exc_info=True)


# ── Sandbox materialization ──────────────────────────────────────────────


def _materialize_sandbox(
    sandbox: Path, repo_root: Path, package: str, to_version: str,
) -> None:
    """Populate *sandbox* with a minimal viable testbed.

    Hardlinks where possible to keep the operation fast — modifies
    only ``requirements.txt`` (which gets a fresh write so the upstream
    copy is unaffected).
    """
    # app/ + tests/ + conftest.py + requirements.txt at minimum
    for sub in ("app", "tests"):
        src = repo_root / sub
        if not src.exists():
            continue
        dst = sandbox / sub
        shutil.copytree(src, dst, symlinks=False, ignore=_ignore_pycache)

    conftest = repo_root / "conftest.py"
    if conftest.exists():
        shutil.copy2(conftest, sandbox / "conftest.py")

    req = repo_root / "requirements.txt"
    if req.exists():
        text = req.read_text(encoding="utf-8")
    else:
        text = ""
    bumped = _bump_requirement(text, package, to_version)
    (sandbox / "requirements.txt").write_text(bumped, encoding="utf-8")


def _ignore_pycache(_dir: str, names: list[str]) -> list[str]:
    return [n for n in names if n == "__pycache__"]
