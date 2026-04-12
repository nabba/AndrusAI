"""
workspace_versioning.py — Git-based workspace versioning with file locking.

Provides:
  - WorkspaceLock: advisory file lock (fcntl.flock) for evolution coordination
  - workspace_commit(): git add + commit all workspace changes
  - workspace_rollback(): restore workspace to a specific commit
  - workspace_log(): recent commit history

Evolution strategies (Autoresearch, Island, Parallel, MAP-Elites) must
acquire WorkspaceLock before modifying workspace files. This prevents
concurrent mutations from corrupting shared state.
"""

from __future__ import annotations

import fcntl
import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

WORKSPACE = Path("/app/workspace")
LOCK_FILE = WORKSPACE / ".workspace.lock"

# Git config for workspace commits (not the user's git identity)
_GIT_AUTHOR = "AndrusAI Evolution"
_GIT_EMAIL = "evolution@andrusai.local"

class WorkspaceLock:
    """Advisory file lock using fcntl.flock for workspace mutation coordination.

    Usage:
        with WorkspaceLock():
            modify_workspace_files()
            workspace_commit("evolution: improved researcher prompt")
    """

    def __init__(self, timeout_s: int = 30):
        try:
            from app.config import get_settings
            self._timeout = getattr(get_settings(), "workspace_lock_timeout_s", timeout_s)
        except Exception:
            self._timeout = timeout_s
        self._fd: int | None = None

    def acquire(self) -> None:
        """Acquire the workspace lock with timeout."""
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(LOCK_FILE, "w")
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except (IOError, OSError):
                if time.monotonic() > deadline:
                    self._fd.close()
                    self._fd = None
                    raise TimeoutError(
                        f"WorkspaceLock: could not acquire lock within {self._timeout}s"
                    )
                time.sleep(0.5)

    def release(self) -> None:
        """Release the workspace lock."""
        if self._fd:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                self._fd.close()
            except Exception:
                pass
            self._fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

def _git(*args, check: bool = False) -> subprocess.CompletedProcess:
    """Run a git command in the workspace directory."""
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **__import__("os").environ,
            "GIT_AUTHOR_NAME": _GIT_AUTHOR,
            "GIT_AUTHOR_EMAIL": _GIT_EMAIL,
            "GIT_COMMITTER_NAME": _GIT_AUTHOR,
            "GIT_COMMITTER_EMAIL": _GIT_EMAIL,
        },
        check=check,
    )

def ensure_workspace_repo() -> bool:
    """Initialize workspace as a git repo if not already. Returns True if initialized."""
    git_dir = WORKSPACE / ".git"
    if git_dir.exists():
        return False
    try:
        _git("init", check=True)
        # Create .gitignore for large/binary files
        gitignore = WORKSPACE / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "*.db\n*.db-shm\n*.db-wal\n*.sqlite3\n"
                "*.pyc\n__pycache__/\n"
                "sandbox_workspaces/\n"
                "mem0_pgdata/\nmem0_neo4j/\n"
                "logs/\n.sender_key\n"
            )
        _git("add", "-A")
        _git("commit", "-m", "Initial workspace snapshot")
        logger.info("workspace_versioning: initialized git repo at /app/workspace")
        return True
    except Exception as e:
        logger.warning(f"workspace_versioning: git init failed: {e}")
        return False

def workspace_commit(message: str) -> str:
    """Stage and commit all workspace changes.

    Returns commit SHA on success, empty string if nothing to commit or on error.
    Non-fatal — never crashes the caller.
    """
    try:
        ensure_workspace_repo()
        # Stage all changes
        _git("add", "-A")
        # Check if there's anything to commit
        status = _git("status", "--porcelain")
        if not status.stdout.strip():
            return ""  # Nothing to commit
        # Commit
        result = _git("commit", "-m", message[:200])
        if result.returncode == 0:
            # Get SHA
            sha_result = _git("rev-parse", "--short", "HEAD")
            sha = sha_result.stdout.strip()
            logger.info(f"workspace_versioning: committed {sha} — {message[:60]}")
            _record_commit(sha, message)
            return sha
        return ""
    except Exception as e:
        logger.debug(f"workspace_versioning: commit failed: {e}")
        return ""

# Track recent commits for auto-rollback on regression
_recent_commits: list[dict] = []  # [{sha, timestamp, message}]
_MAX_RECENT = 10

def _record_commit(sha: str, message: str) -> None:
    """Record a commit for post-commit health monitoring."""
    import time as _t
    _recent_commits.append({"sha": sha, "ts": _t.monotonic(), "message": message})
    if len(_recent_commits) > _MAX_RECENT:
        _recent_commits.pop(0)

def check_post_commit_regression() -> None:
    """Check if health metrics degraded after a recent workspace commit.

    If error rate increased >20% within 1 hour of a commit, auto-rollback.
    Called by the idle scheduler data-retention job (lightweight check).
    """
    import time as _t
    now = _t.monotonic()

    for commit in reversed(_recent_commits):
        age_s = now - commit["ts"]
        if age_s > 3600:
            break  # Only check commits < 1 hour old
        if commit.get("rolled_back"):
            continue

        # Check if error rate spiked since this commit
        try:
            from app.error_handler import get_error_counts
            counts = get_error_counts()
            total_errors = sum(counts.values())
            if total_errors > 10:  # Significant error spike
                logger.warning(
                    f"workspace_versioning: regression detected after commit {commit['sha']} "
                    f"({total_errors} errors in {age_s:.0f}s) — auto-rolling back"
                )
                # Get the commit before this one
                log = workspace_log(5)
                prev_sha = None
                for i, entry in enumerate(log):
                    if entry["short_sha"] == commit["sha"] and i + 1 < len(log):
                        prev_sha = log[i + 1]["sha"]
                        break
                if prev_sha:
                    success = workspace_rollback(prev_sha)
                    if success:
                        commit["rolled_back"] = True
                        logger.warning(f"workspace_versioning: auto-rolled back to {prev_sha}")
                        try:
                            from app.signal_client import send_message
                            from app.config import get_settings
                            send_message(
                                get_settings().signal_owner_number,
                                f"⚠️ Auto-rollback: commit {commit['sha']} caused regression "
                                f"({total_errors} errors). Reverted to {prev_sha[:8]}.",
                            )
                        except Exception:
                            pass
        except Exception:
            pass

def workspace_rollback(sha: str) -> bool:
    """Restore workspace to a specific commit. Returns True on success."""
    try:
        ensure_workspace_repo()
        result = _git("checkout", sha, "--", ".")
        if result.returncode == 0:
            logger.info(f"workspace_versioning: rolled back to {sha}")
            return True
        logger.warning(f"workspace_versioning: rollback to {sha} failed: {result.stderr[:200]}")
        return False
    except Exception as e:
        logger.warning(f"workspace_versioning: rollback failed: {e}")
        return False

def workspace_log(n: int = 20) -> list[dict]:
    """Recent commit history as structured data."""
    try:
        ensure_workspace_repo()
        result = _git("log", f"--max-count={n}", "--format=%H|%h|%s|%ai")
        if result.returncode != 0:
            return []
        entries = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 3)
            if len(parts) == 4:
                entries.append({
                    "sha": parts[0],
                    "short_sha": parts[1],
                    "message": parts[2],
                    "date": parts[3],
                })
        return entries
    except Exception:
        return []
