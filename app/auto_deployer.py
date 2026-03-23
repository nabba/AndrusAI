"""
auto_deployer.py — Automatic deployment of auditor/self-heal code fixes.

When the auditor or error resolution loop writes fixed files to
/app/workspace/applied_code/, this module:

1. Copies them over the live source in /app/
2. Triggers a hot-reload of the FastAPI app (if uvicorn supports it)
3. Falls back to signaling the user via Signal if hot-reload isn't available

Safety measures:
  - Backs up original files before overwriting
  - Validates Python syntax before applying
  - Keeps a deploy log for rollback reference
  - Only processes files under app/ (never entrypoint, Dockerfile, etc.)
"""

import ast
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

APPLIED_CODE_DIR = Path("/app/workspace/applied_code")
LIVE_CODE_DIR = Path("/app")
BACKUP_DIR = Path("/app/workspace/deploy_backups")
DEPLOY_LOG = Path("/app/workspace/deploy_log.json")

# Modules that LLM-generated code must never import — prevents code execution
# attacks, credential theft, network exfiltration, etc.
_BLOCKED_IMPORTS = frozenset({
    "subprocess", "os", "sys", "shutil",
    "ctypes", "importlib", "pickle", "shelve", "marshal",
    "socket", "http.server", "xmlrpc", "ftplib", "smtplib",
    "webbrowser", "code", "codeop", "compileall",
    "pty", "resource", "sysconfig",
    "yaml", "signal", "multiprocessing", "tempfile",
})

# Also block builtins used in code: eval(), exec(), compile(), __import__()
_BLOCKED_CALLS = frozenset({"eval", "exec", "compile", "__import__", "getattr",
                            "globals", "locals", "vars", "dir", "delattr", "setattr"})

# Files that self-modification systems (evolution, self-heal, auditor) must
# NEVER be allowed to modify.  These enforce the security boundary.
PROTECTED_FILES = frozenset({
    "app/sanitize.py",
    "app/security.py",
    "app/vetting.py",
    "app/auto_deployer.py",
    "app/rate_throttle.py",
    "app/circuit_breaker.py",
    "app/config.py",
    "app/main.py",
    "app/experiment_runner.py",
    "app/evolution.py",
    "app/proposals.py",
    "app/signal_client.py",
    "app/firebase_reporter.py",
    "entrypoint.sh",
    "Dockerfile",
    "docker-compose.yml",
    "dashboard/firestore.rules",
})


def validate_proposal_paths(files: dict[str, str]) -> list[str]:
    """Validate all file paths in a proposal. Returns list of violations.

    Checks:
      - No path traversal (.. or absolute paths)
      - No protected files
      - Only allowed directories (app/, skills/, or bare filenames like skill.md)
    """
    violations = []
    for fpath in files:
        # Block path traversal
        if ".." in fpath or fpath.startswith("/"):
            violations.append(f"Path traversal blocked: {fpath}")
            continue
        # Normalize
        normalized = str(Path(fpath))
        # Block protected files
        if normalized in PROTECTED_FILES:
            violations.append(f"Protected file: {normalized}")
            continue
        # Allow: app/ subdirs, skills/ subdir, or bare filenames (skills, configs)
        has_dir = "/" in normalized
        if has_dir and not (normalized.startswith("app/") or normalized.startswith("skills/")):
            violations.append(f"Outside allowed directories: {normalized}")
    return violations


# Constitutional invariants — evolved code must NEVER remove these.
# Checked at deploy time (not just AST safety).
_CONSTITUTIONAL_IMPORTS = {
    # Security essentials that must never be removed from a file that has them
    "app.sanitize": ["sanitize_input", "wrap_user_input"],
    "app.vetting": ["vet_response"],
    "app.security": ["is_authorized_sender"],
}


def _check_constitutional_invariants(src_path: Path, new_source: str) -> list[str]:
    """Verify that evolved code doesn't remove constitutional security imports.

    Compares the new source against the existing live file. If the live file
    imports a constitutional module and the new version doesn't, it's blocked.
    """
    violations = []
    dest = LIVE_CODE_DIR / src_path
    if not dest.exists():
        return []  # new file, no baseline to compare

    try:
        old_source = dest.read_text()
    except OSError:
        return []

    for module, functions in _CONSTITUTIONAL_IMPORTS.items():
        # Check if the old file imported this module
        if module in old_source:
            # Verify the new file still imports it
            if module not in new_source:
                violations.append(
                    f"Constitutional violation: removed import of {module}"
                )
            # Check individual functions
            for func in functions:
                if func in old_source and func not in new_source:
                    violations.append(
                        f"Constitutional violation: removed {func} from {module}"
                    )
    return violations


def _check_dangerous_imports(tree: ast.AST) -> list[str]:
    """Scan AST for dangerous imports and calls. Returns list of violations."""
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _BLOCKED_IMPORTS or alias.name.split(".")[0] in _BLOCKED_IMPORTS:
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in _BLOCKED_IMPORTS or mod.split(".")[0] in _BLOCKED_IMPORTS:
                violations.append(f"from {mod} import ...")
        elif isinstance(node, ast.Call):
            # Check for eval(), exec(), compile(), __import__()
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in _BLOCKED_CALLS:
                violations.append(f"{name}() call")
    return violations


_deploy_lock = threading.Lock()
_deploy_scheduled = False


def schedule_deploy(reason: str) -> None:
    """
    Schedule a deploy to run shortly. Multiple calls are de-duped — only
    one deploy runs per batch of changes.
    """
    global _deploy_scheduled
    with _deploy_lock:
        if _deploy_scheduled:
            return
        _deploy_scheduled = True

    logger.info(f"auto_deployer: deploy scheduled — {reason}")

    import time

    def _delayed():
        global _deploy_scheduled
        time.sleep(5)  # wait for all file writes to complete
        try:
            run_deploy(reason)
        finally:
            with _deploy_lock:
                _deploy_scheduled = False

    t = threading.Thread(target=_delayed, daemon=True, name="auto-deploy")
    t.start()


def run_deploy(reason: str = "manual") -> str:
    """
    Apply all files from applied_code/ to the live codebase.
    Returns a status message.
    """
    with _deploy_lock:
        return _deploy_locked(reason)


def _deploy_locked(reason: str) -> str:
    if not APPLIED_CODE_DIR.exists():
        return "No applied_code directory."

    # Find all files to deploy
    files_to_deploy = []
    for f in APPLIED_CODE_DIR.rglob("*.py"):
        rel = f.relative_to(APPLIED_CODE_DIR)
        # Only allow files under app/ for safety
        if not str(rel).startswith("app/"):
            logger.warning(f"auto_deployer: skipping non-app file: {rel}")
            continue
        files_to_deploy.append((f, rel))

    if not files_to_deploy:
        return "No files to deploy."

    # Validate all files have valid Python syntax and no dangerous imports
    invalid = []
    dangerous = []
    for src, rel in files_to_deploy:
        source = src.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            invalid.append(f"{rel}: {e}")
            continue
        # Check for dangerous imports that LLM-generated code should never use
        blocked = _check_dangerous_imports(tree)
        if blocked:
            dangerous.append(f"{rel}: {', '.join(blocked)}")

    if invalid:
        msg = f"Deploy blocked: {len(invalid)} files have syntax errors: {'; '.join(invalid[:3])}"
        logger.error(f"auto_deployer: {msg}")
        _log_deploy("blocked", reason, [], msg)
        return msg

    if dangerous:
        msg = f"Deploy blocked: dangerous imports in {'; '.join(dangerous[:3])}"
        logger.error(f"auto_deployer: {msg}")
        _log_deploy("blocked", reason, [], msg)
        return msg

    # Step 8: Constitutional invariant check — evolved code must not remove security imports
    constitutional = []
    for src, rel in files_to_deploy:
        violations = _check_constitutional_invariants(rel, src.read_text())
        constitutional.extend(violations)
    if constitutional:
        msg = f"Deploy blocked: {'; '.join(constitutional[:3])}"
        logger.error(f"auto_deployer: {msg}")
        _log_deploy("blocked", reason, [], msg)
        return msg

    # Create timestamped backup
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / ts
    backup.mkdir(parents=True, exist_ok=True)

    deployed = []
    for src, rel in files_to_deploy:
        dest = LIVE_CODE_DIR / rel

        # Backup original
        if dest.exists():
            backup_file = backup / rel
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(dest, backup_file)
            except OSError:
                pass  # backup is best-effort

        # Copy new version — may fail on read-only filesystem (Docker read_only: true)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            deployed.append(str(rel))
            logger.info(f"auto_deployer: deployed {rel}")
        except OSError as exc:
            # R2: Read-only filesystem — log clearly so user knows to remove
            # read_only:true from docker-compose.yml if they want code self-modification.
            logger.warning(
                f"auto_deployer: cannot deploy {rel} — filesystem is read-only. "
                f"Remove 'read_only: true' from docker-compose.yml gateway service "
                f"to enable code self-modification. Error: {exc}"
            )
            _log_deploy("blocked", reason, [], f"Read-only filesystem: {rel}")
            return (
                f"Deploy blocked: filesystem is read-only. "
                f"To enable code self-modification, remove 'read_only: true' "
                f"from the gateway service in docker-compose.yml and restart."
            )

    # Clean up applied_code (files have been deployed)
    for src, rel in files_to_deploy:
        try:
            src.unlink()
        except OSError:
            pass
    # Remove empty directories
    _cleanup_empty_dirs(APPLIED_CODE_DIR)

    msg = f"Deployed {len(deployed)} files: {', '.join(deployed)}"
    _log_deploy("success", reason, deployed)
    logger.info(f"auto_deployer: {msg}")

    # R2: Hot-reload deployed modules so changes take effect without restart.
    # Uses importlib.reload() for each modified module. This is imperfect
    # (circular imports, cached references) but handles simple fixes.
    reloaded = _hot_reload_modules(deployed)
    if reloaded:
        msg += f" Hot-reloaded: {', '.join(reloaded)}"
        logger.info(f"auto_deployer: hot-reloaded {len(reloaded)} modules")

    # Notify via Firebase
    try:
        from app.firebase_reporter import _get_db, _now_iso
        db = _get_db()
        if db:
            db.collection("activities").add({
                "ts": _now_iso(),
                "event": "auto_deploy",
                "crew": "auditor",
                "detail": msg,
            })
    except Exception:
        pass

    return msg


def _hot_reload_modules(deployed_files: list[str]) -> list[str]:
    """Attempt to hot-reload deployed Python modules.

    R2: Converts file paths like 'app/tools/web_search.py' to module names
    like 'app.tools.web_search' and calls importlib.reload(). Returns list
    of successfully reloaded module names.

    This is best-effort — some modules may have cached references that
    won't update. But for simple fixes (adding error handling, changing
    constants, fixing logic), it works without restart.
    """
    import importlib
    import sys

    reloaded = []
    for filepath in deployed_files:
        if not filepath.endswith(".py"):
            continue
        # Convert path to module name: "app/tools/web_search.py" → "app.tools.web_search"
        module_name = filepath[:-3].replace("/", ".")
        if module_name in sys.modules:
            try:
                importlib.reload(sys.modules[module_name])
                reloaded.append(module_name)
            except Exception as exc:
                logger.warning(f"auto_deployer: reload failed for {module_name}: {exc}")
    return reloaded


def _cleanup_empty_dirs(root: Path) -> None:
    """Remove empty directories recursively."""
    for d in sorted(root.rglob("*"), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()  # only succeeds if empty
            except OSError:
                pass


def _log_deploy(status: str, reason: str, files: list, error: str = "") -> None:
    """Append to deploy log."""
    import json
    try:
        log = []
        if DEPLOY_LOG.exists():
            log = json.loads(DEPLOY_LOG.read_text())
        log.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "reason": reason[:200],
            "files": files,
            "error": error[:200],
        })
        DEPLOY_LOG.write_text(json.dumps(log[-100:], indent=2))
    except (OSError, json.JSONDecodeError):
        pass


def get_deploy_log(n: int = 10) -> str:
    """Return recent deploy activity."""
    import json
    try:
        if DEPLOY_LOG.exists():
            log = json.loads(DEPLOY_LOG.read_text())[-n:]
            if not log:
                return "No deployments yet."
            lines = []
            for e in log:
                files_str = ", ".join(e.get("files", [])[:3])
                lines.append(
                    f"[{e['ts'][:16]}] {e['status']}: {e.get('reason', '')[:60]} "
                    f"({files_str})"
                )
            return "\n".join(lines)
    except (OSError, json.JSONDecodeError):
        pass
    return "No deployments yet."
