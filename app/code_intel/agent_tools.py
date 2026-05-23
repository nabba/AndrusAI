"""Agent-callable code-intel tools (Phase 3 piece 1b, 2026-05-20).

Three thin `@tool`-decorated wrappers around the v1 query API. Each
returns a concise, operator-readable string the LLM can parse —
matching the convention used by other agent tools (``file_manager``,
``signal_send_attachment``, etc.).

Why string output instead of structured?

The CrewAI runtime treats tool results as text appended to the agent's
context. A structured dict would be JSON-serialised and the agent
would see the same characters either way; a one-line-per-match string
is easier for the LLM to scan and reason about.

Wire-up
───────

These tools are NOT auto-registered via ``@register_tool`` because
the closest existing capability tag is ``reads-file`` and a
dedicated ``reads-source-code`` capability requires a Tier-3 amendment
to ``app/tool_registry/capabilities.py``. They are however available
as ``@tool``-decorated functions; agents that want them list them
explicitly in their tool inventory. The coder agent is the natural
first consumer.

The tools return early with a clear "index not built yet" message
when ``is_built()`` reports the snapshot file is missing — so the
agent sees an actionable diagnostic rather than a confusing empty
list.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# The CrewAI ``tool`` decorator is what the agents see; we lazy-import
# it so this module is testable without the heavy crewai bootstrap.
try:
    from crewai.tools import tool
except Exception:  # pragma: no cover — stripped test env
    def tool(name: str):
        def _decorator(fn):
            return fn
        return _decorator


# Output caps: agent context budget guard. Each result line is ~80
# chars; 25 lines is ~2000 chars which is plenty for a typical
# "where is X" question.
_MAX_LINES = 25
_NOT_BUILT_MSG = (
    "Code-intel index has not been built yet. Either wait for the "
    "next scheduler refresh (HEAVY job 'code-intel-refresh' fires "
    "every 30 minutes when code_intel_enabled=True) or invoke "
    "app.code_intel.refresh.run_refresh(force=True) operator-side."
)


def _format_symbol_line(s) -> str:
    """Render one SymbolLocation as a compact agent-readable line."""
    kind = s.kind.value
    fq = s.fully_qualified
    doc = s.docstring_first_line
    if doc:
        return f"  [{kind:14s}] {fq:<40s} {s.file_path}:{s.lineno}  — {doc}"
    return f"  [{kind:14s}] {fq:<40s} {s.file_path}:{s.lineno}"


def _format_reference_line(r) -> str:
    """Render one ReferenceLocation as a compact line."""
    ctx = ""
    if r.in_class and r.in_function:
        ctx = f"in {r.in_class}.{r.in_function} "
    elif r.in_function:
        ctx = f"in {r.in_function} "
    elif r.in_class:
        ctx = f"in class {r.in_class} "
    return f"  {r.file_path}:{r.lineno}  {ctx}".rstrip()


def _truncate_for_output(items: list, formatter, header: str) -> str:
    """Compose a multi-line tool response from a list of items.
    Caps at ``_MAX_LINES`` with an explicit "(N more truncated)"
    footer so the agent knows there's more."""
    if not items:
        return f"{header} (no matches)"
    lines = [header]
    for item in items[:_MAX_LINES]:
        lines.append(formatter(item))
    if len(items) > _MAX_LINES:
        lines.append(
            f"  … and {len(items) - _MAX_LINES} more (use file_prefix "
            f"to narrow the search)"
        )
    return "\n".join(lines)


@tool("code_intel_find_symbol")
def code_intel_find_symbol(
    name: str,
    file_prefix: str = "",
) -> str:
    """
    Find every definition site of a function / class / method named NAME.

    Args:
      name: Exact symbol name to look up (case-sensitive). e.g.
        ``"submit_session"`` or ``"CodingSession"``.
      file_prefix: Optional path prefix to narrow results. e.g.
        ``"app/agents/"`` to only look at agent code.

    Returns a multi-line summary, one definition per line, with the
    file path, line number, parent class (for methods), and the
    first line of the docstring when present.

    Use this BEFORE grepping the codebase — the symbol index has
    structured information about every function and class and is
    much faster than a text search.

    Example output::

        code_intel_find_symbol("submit_session")
        → 1 definition for 'submit_session':
            [function       ] submit_session                            app/coding_session/submit.py:127  — Discover the worktree's changes...
    """
    from app.code_intel import find_symbol, is_built

    if not isinstance(name, str):
        return f"Error: name must be a string (got {type(name).__name__})"
    cleaned = name.strip()
    if not cleaned:
        return "Error: name cannot be empty"
    if not is_built():
        return _NOT_BUILT_MSG

    try:
        results = find_symbol(
            cleaned,
            file_prefix=file_prefix.strip() or None,
        )
    except Exception as exc:
        return f"code_intel_find_symbol failed: {type(exc).__name__}: {exc}"

    header = f"{len(results)} definition(s) for {cleaned!r}:"
    return _truncate_for_output(results, _format_symbol_line, header)


@tool("code_intel_find_references")
def code_intel_find_references(
    name: str,
    file_prefix: str = "",
) -> str:
    """
    Find every usage of NAME across the indexed Python files.

    Args:
      name: Exact identifier to look up (case-sensitive). Matches both
        plain references (``foo()``) and attribute references
        (``module.foo``).
      file_prefix: Optional path prefix to narrow results.

    Returns one line per reference, with the enclosing
    function/class context where available.

    Use this for "who uses this thing?" questions before refactoring.
    Combine with file_prefix to scope the search ("who in
    app/agents/ calls send_signal?").

    Example::

        code_intel_find_references("create_request")
        → 8 reference(s) to 'create_request':
            app/change_requests/api.py:42   in approve
            app/change_requests/api.py:67   in reject
            ...
    """
    from app.code_intel import find_references, is_built

    if not isinstance(name, str):
        return f"Error: name must be a string (got {type(name).__name__})"
    cleaned = name.strip()
    if not cleaned:
        return "Error: name cannot be empty"
    if not is_built():
        return _NOT_BUILT_MSG

    try:
        results = find_references(
            cleaned,
            file_prefix=file_prefix.strip() or None,
        )
    except Exception as exc:
        return (
            f"code_intel_find_references failed: "
            f"{type(exc).__name__}: {exc}"
        )

    header = f"{len(results)} reference(s) to {cleaned!r}:"
    return _truncate_for_output(results, _format_reference_line, header)


@tool("code_intel_find_callers")
def code_intel_find_callers(
    func_name: str,
    file_prefix: str = "",
) -> str:
    """
    Find every function/method that calls FUNC_NAME.

    Args:
      func_name: Exact name of the function being called.
      file_prefix: Optional path prefix to narrow results.

    Returns the caller-function definitions (one per line) — same
    shape as find_symbol but filtered to the set of functions that
    contain a reference to FUNC_NAME.

    This is the "blast radius" view: before changing a function's
    signature or behaviour, list its callers so you know what may
    need to update too.

    Module-level calls (not inside a function) are omitted because
    there's no caller-function symbol to attribute. Use
    code_intel_find_references for the unfiltered usage list.

    Example::

        code_intel_find_callers("submit_session")
        → 3 caller(s) of 'submit_session':
            [function       ] submit                                    app/tools/coding_session_tools.py:512
            [function       ] _handle_submit                            app/agents/coder.py:88
            ...
    """
    from app.code_intel import find_callers, is_built

    if not isinstance(func_name, str):
        return (
            f"Error: func_name must be a string "
            f"(got {type(func_name).__name__})"
        )
    cleaned = func_name.strip()
    if not cleaned:
        return "Error: func_name cannot be empty"
    if not is_built():
        return _NOT_BUILT_MSG

    try:
        results = find_callers(
            cleaned,
            file_prefix=file_prefix.strip() or None,
        )
    except Exception as exc:
        return (
            f"code_intel_find_callers failed: "
            f"{type(exc).__name__}: {exc}"
        )

    header = f"{len(results)} caller(s) of {cleaned!r}:"
    return _truncate_for_output(results, _format_symbol_line, header)


# Type-check output cap. Pyright can produce dozens of diagnostics
# per file; the agent rarely benefits from seeing more than the top
# few — over the cap, we tail-truncate and tell the agent to filter.
_MAX_TYPE_LINES = 20


def _format_type_diagnostic_line(d) -> str:
    """Render one ``PyrightDiagnostic`` as a compact agent-readable line."""
    sev = d.severity
    icon = {"error": "❌", "warning": "⚠", "info": "ℹ"}.get(sev, "·")
    rule = d.rule or "—"
    return (
        f"  {icon} [{sev:7s}] {d.file}:{d.line}:{d.column}  "
        f"[{rule}]  {d.message}"
    )


@tool("code_intel_type_check")
def code_intel_type_check(
    workspace_relative_path: str,
) -> str:
    """
    Type-check WORKSPACE_RELATIVE_PATH with pyright and return a compact
    summary of any errors / warnings.

    Args:
      workspace_relative_path: Path of the file to check, relative to
        the project root. e.g. ``"app/coding_session/iterate.py"``.

    Returns a multi-line summary, one diagnostic per line, grouped
    error → warning → info. Lines look like::

        ❌ [error  ] app/x.py:10:5  [reportGeneralTypeIssues]  Cannot assign int to str

    Use this AFTER editing a Python file to confirm the edit
    type-checks before submitting. Composes with the iterate-until-
    green loop and the coding-session submit step — calling here
    gives the agent the same signal those loops will compute when
    deciding whether to accept the change.

    Failure modes the agent will see (never raises):
      * "Pyright sidecar is disabled ..." — master switch off, no
        type-check available; the agent should proceed without it.
      * "Pyright binary not on PATH ..." — sidecar enabled but
        binary missing; operator needs to install pyright.
      * "Pyright timed out ..." — usually a sign of a large file or
        a slow host; agent should narrow the scope.
      * "(no diagnostics)" — file is type-clean.
    """
    from app.code_intel import pyright_is_available, check_file

    if not isinstance(workspace_relative_path, str):
        return (
            f"Error: workspace_relative_path must be a string "
            f"(got {type(workspace_relative_path).__name__})"
        )
    cleaned = workspace_relative_path.strip()
    if not cleaned:
        return "Error: workspace_relative_path cannot be empty"
    if cleaned.startswith("/"):
        return (
            "Error: absolute paths refused; use a workspace-relative "
            "path (e.g. 'app/x.py' not '/work/app/x.py')"
        )
    if ".." in cleaned.split("/"):
        return "Error: parent-traversal ('..') refused in path"

    # Resolve against the project root so the sidecar sees an absolute
    # path. The bridge would resolve against a worktree in production;
    # for the agent surface we use the gateway's cwd as a reasonable
    # default — the operator can override by passing an already-
    # absolute path (rejected above to keep the contract clean).
    from pathlib import Path
    target = Path(cleaned)

    try:
        report = check_file(target)
    except Exception as exc:
        return (
            f"code_intel_type_check failed: {type(exc).__name__}: {exc}"
        )

    if report.disabled:
        return (
            "Pyright sidecar is disabled (master switch "
            "pyright_sidecar_enabled is OFF). Operator needs to flip "
            "it on in /cp/settings before this tool returns useful "
            "results."
        )
    if not report.available:
        return (
            "Pyright binary not on PATH. Operator needs to install "
            "pyright (e.g. `npm install -g pyright`) in the gateway "
            "image before this tool returns useful results."
        )
    if report.timed_out:
        return (
            f"Pyright timed out after {report.duration_s:.1f}s on "
            f"{cleaned!r}. Try a smaller file or rerun later."
        )
    if report.error:
        return (
            f"Pyright sidecar reported an error: {report.error}"
        )

    diagnostics = report.diagnostics
    if not diagnostics:
        return f"Type-check {cleaned!r}: (no diagnostics)"

    # Group error → warning → info so the agent sees the most
    # important rows first within the cap.
    severity_rank = {"error": 0, "warning": 1, "info": 2}
    sorted_diags = sorted(
        diagnostics,
        key=lambda d: (severity_rank.get(d.severity, 99), d.line),
    )

    header_bits = [f"{len(diagnostics)} diagnostic(s) for {cleaned!r}"]
    if report.errors:
        header_bits.append(f"{len(report.errors)} error(s)")
    if report.warnings:
        header_bits.append(f"{len(report.warnings)} warning(s)")
    header = ", ".join(header_bits) + ":"

    lines = [header]
    for d in sorted_diags[:_MAX_TYPE_LINES]:
        lines.append(_format_type_diagnostic_line(d))
    if len(sorted_diags) > _MAX_TYPE_LINES:
        lines.append(
            f"  … and {len(sorted_diags) - _MAX_TYPE_LINES} more (fix "
            f"the errors above first; warnings rarely block submit)"
        )
    return "\n".join(lines)


# ── Phase C.2 v2 query tools (2026-05-22) ────────────────────────────


@tool("code_intel_coverage")
def code_intel_coverage(
    name: str,
    test_root: str = "tests/",
) -> str:
    """
    Find every reference to NAME inside the test tree — i.e. which
    tests exercise this symbol.

    Args:
      name: Exact symbol name (case-sensitive). e.g.
        ``"submit_session"`` or ``"CodingSession"``.
      test_root: Path prefix that contains the test files. Defaults
        to ``"tests/"`` which matches the project layout. Pass an
        alternate prefix only for non-standard test trees.

    Returns one line per test-side reference, with the enclosing
    function/class context where available.

    Use this BEFORE changing a function: the answer is the set of
    tests most likely to catch a regression in your edit. Combine
    with ``code_intel_find_callers`` for the production-code side of
    the same question.

    Example::

        code_intel_coverage("create_request")
        → 5 test reference(s) to 'create_request' under 'tests/':
            tests/test_change_requests_lifecycle.py:42  in test_create_request_happy_path
            tests/test_change_requests_lifecycle.py:71  in test_create_request_refuses_tier_immutable
            ...
    """
    from app.code_intel import find_test_coverage, is_built

    if not isinstance(name, str):
        return f"Error: name must be a string (got {type(name).__name__})"
    cleaned = name.strip()
    if not cleaned:
        return "Error: name cannot be empty"
    if not isinstance(test_root, str):
        return (
            f"Error: test_root must be a string "
            f"(got {type(test_root).__name__})"
        )
    cleaned_root = test_root.strip()
    if not cleaned_root:
        return "Error: test_root cannot be empty"
    if not is_built():
        return _NOT_BUILT_MSG

    try:
        results = find_test_coverage(cleaned, test_root=cleaned_root)
    except Exception as exc:
        return (
            f"code_intel_coverage failed: "
            f"{type(exc).__name__}: {exc}"
        )

    header = (
        f"{len(results)} test reference(s) to {cleaned!r} "
        f"under {cleaned_root!r}:"
    )
    return _truncate_for_output(results, _format_reference_line, header)


@tool("code_intel_deps")
def code_intel_deps(
    file_path: str,
) -> str:
    """
    List every module FILE_PATH imports, sorted + deduplicated.

    Args:
      file_path: Workspace-relative path of a Python file. e.g.
        ``"app/coding_session/iterate.py"``.

    Returns the file's import surface, one module per line. Relative
    imports are surfaced in their relative form (``.sibling``,
    ``..parent.thing``) so the caller can decide whether to resolve.

    Use this for "what does this module depend on?" questions when
    auditing coupling, or before extracting a module to a different
    layer.

    The implementation re-parses the file on demand (the index
    doesn't currently track imports). Returns an empty list when the
    file doesn't exist, is unreadable, or doesn't parse — the agent
    sees a clear "(no imports found)" footer in that case.

    Example::

        code_intel_deps("app/coding_session/iterate.py")
        → 8 module(s) imported by 'app/coding_session/iterate.py':
            app.code_intel
            app.coding_session.runner
            dataclasses
            logging
            pathlib
            ...
    """
    from app.code_intel import find_module_deps

    if not isinstance(file_path, str):
        return (
            f"Error: file_path must be a string "
            f"(got {type(file_path).__name__})"
        )
    cleaned = file_path.strip()
    if not cleaned:
        return "Error: file_path cannot be empty"
    if cleaned.startswith("/"):
        return (
            "Error: absolute paths refused; use a workspace-relative "
            "path (e.g. 'app/x.py' not '/work/app/x.py')"
        )
    if ".." in cleaned.split("/"):
        return "Error: parent-traversal ('..') refused in path"
    if not cleaned.endswith(".py"):
        return (
            f"Error: file_path must end in '.py' (got {cleaned!r}); "
            f"only Python files have AST-extractable imports"
        )

    try:
        deps = find_module_deps(cleaned)
    except Exception as exc:
        return (
            f"code_intel_deps failed: "
            f"{type(exc).__name__}: {exc}"
        )

    if not deps:
        return f"{cleaned!r}: (no imports found)"

    header = f"{len(deps)} module(s) imported by {cleaned!r}:"
    lines = [header]
    for mod in deps[:_MAX_LINES]:
        lines.append(f"  {mod}")
    if len(deps) > _MAX_LINES:
        lines.append(
            f"  … and {len(deps) - _MAX_LINES} more"
        )
    return "\n".join(lines)


# ── Verified Plan §5 Gap C closure (2026-05-23) ─────────────────────
# Two additional tools the original plan listed: ``history`` (git
# blame / change log for a symbol or file) and ``test_for`` (find
# tests covering a given symbol/file). Both read-only.

import re as _re
import subprocess as _subprocess
from pathlib import Path as _Path


_HISTORY_MAX_COMMITS = 10
_HISTORY_TIMEOUT_S = 15


@tool("code_intel_history")
def code_intel_history(
    file_path: str,
    *,
    max_commits: int = _HISTORY_MAX_COMMITS,
    line: Optional[int] = None,
) -> str:
    """Return the recent git change history for ``file_path``.

    Implementation: subprocess wrapper around ``git log --follow
    --pretty=format:'%h | %an | %ar | %s' -n <max_commits> <path>``.
    When ``line`` is provided, also runs ``git blame -L <line>,<line>``
    to identify the author + commit that last touched that line.

    Read-only; never modifies any git state. Failure-isolated — a
    missing path, non-git workspace, or absent ``git`` binary returns
    a clear one-line diagnostic, not an exception.

    Args:
      file_path: Workspace-relative or absolute path.
      max_commits: Cap on log entries (default 10; max 50).
      line: Optional 1-indexed line number for blame lookup.
    """
    if not file_path or not isinstance(file_path, str):
        return "code_intel_history: empty file_path"
    cap = max(1, min(int(max_commits or 10), 50))

    # Resolve to repo-relative path; we run git from repo root.
    repo_root = _Path(__file__).resolve().parents[2]
    target = (_Path(file_path) if _Path(file_path).is_absolute()
              else repo_root / file_path)
    if not target.exists():
        return f"code_intel_history: {file_path!r} does not exist"

    try:
        rel = str(target.resolve().relative_to(repo_root))
    except ValueError:
        # Outside repo — abort rather than scan arbitrary paths.
        return (
            f"code_intel_history: {file_path!r} is outside the repo "
            f"({repo_root}); refusing."
        )

    try:
        log_proc = _subprocess.run(
            [
                "git", "log", "--follow",
                "--pretty=format:%h | %an | %ar | %s",
                "-n", str(cap), "--", rel,
            ],
            cwd=str(repo_root), capture_output=True, text=True,
            timeout=_HISTORY_TIMEOUT_S,
        )
    except (FileNotFoundError, _subprocess.TimeoutExpired) as exc:
        return f"code_intel_history: git invocation failed ({exc})"
    except Exception as exc:  # noqa: BLE001
        return (
            f"code_intel_history: unexpected error "
            f"({type(exc).__name__}: {exc})"
        )

    if log_proc.returncode != 0:
        stderr = (log_proc.stderr or "").strip()[:200]
        return f"code_intel_history: git log exited {log_proc.returncode}: {stderr}"

    lines = [f"📜 {rel} — last {cap} commits"]
    out = (log_proc.stdout or "").strip()
    if not out:
        lines.append("  (no git history found for this path)")
    else:
        for entry in out.splitlines():
            lines.append(f"  {entry}")

    # Optional blame lookup for a specific line.
    if line is not None:
        try:
            ln = int(line)
            if ln < 1:
                raise ValueError("line must be >= 1")
            blame = _subprocess.run(
                [
                    "git", "blame", "-L", f"{ln},{ln}",
                    "--porcelain", rel,
                ],
                cwd=str(repo_root), capture_output=True, text=True,
                timeout=_HISTORY_TIMEOUT_S,
            )
            if blame.returncode == 0:
                # Porcelain header: "<sha> <orig> <final> <count>"
                header = (blame.stdout or "").split("\n", 1)[0]
                sha = header.split(" ", 1)[0] if header else ""
                lines.append(
                    f"\n  blame L{ln}: {sha[:12]}"
                )
        except (ValueError, _subprocess.TimeoutExpired):
            pass
        except Exception:
            pass

    return "\n".join(lines)


# Match common test-file naming conventions; tunable later if we add
# JS/Go which use different prefixes (e.g. ``foo.test.ts``,
# ``foo_test.go``).
_TEST_FILE_RE = _re.compile(
    r"(^|/)(test_[\w]+\.py|[\w]+_test\.py|tests/[\w/]+\.py)$",
    _re.IGNORECASE,
)


@tool("code_intel_test_for")
def code_intel_test_for(
    symbol_or_path: str,
    *,
    max_results: int = 20,
) -> str:
    """Find tests that cover a given symbol or file.

    Walks the ``tests/`` directory and the code_intel reference index
    for any test file that imports the module containing ``symbol`` OR
    calls ``symbol`` directly. Returns a deduplicated list of test
    files ranked by reference count.

    Read-only; never executes tests. Useful for the agent's
    "what should I run after editing X" question.

    Args:
      symbol_or_path: A symbol name (e.g. ``gate_output``) OR a
        workspace-relative file path (e.g.
        ``app/epistemic/orchestrator_hook.py``).
      max_results: Cap on returned test files (default 20).
    """
    if not symbol_or_path or not isinstance(symbol_or_path, str):
        return "code_intel_test_for: empty argument"
    cap = max(1, min(int(max_results or 20), 100))

    repo_root = _Path(__file__).resolve().parents[2]
    tests_dir = repo_root / "tests"
    if not tests_dir.exists():
        return (
            f"code_intel_test_for: no ``tests/`` directory at "
            f"{tests_dir} — nothing to scan"
        )

    # Decide: symbol-name lookup OR file-path lookup?
    is_path = "/" in symbol_or_path or symbol_or_path.endswith(".py")

    matches: dict[str, int] = {}  # rel_path → match_count

    if is_path:
        # Derive module path: app/foo/bar.py → app.foo.bar
        target = symbol_or_path
        if target.endswith(".py"):
            target = target[:-3]
        module_path = target.replace("/", ".")
        # Strip leading ./
        module_path = module_path.lstrip(".")
        # Search for `import app.foo.bar` or `from app.foo.bar import`
        needles = (
            f"import {module_path}",
            f"from {module_path}",
        )
    else:
        # Bare symbol — search for the name as a token.
        needles = (
            f" {symbol_or_path}(",
            f"({symbol_or_path}(",
            f"={symbol_or_path}(",
            f"\t{symbol_or_path}(",
            f" {symbol_or_path} ",
            f"({symbol_or_path})",
            f" {symbol_or_path},",
            f" {symbol_or_path}\n",
        )

    # Walk tests/ for test files.
    for path in tests_dir.rglob("*.py"):
        rel_str = str(path.relative_to(repo_root))
        if not _TEST_FILE_RE.search(rel_str):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        count = sum(content.count(n) for n in needles)
        if count > 0:
            matches[rel_str] = count

    if not matches:
        kind = "module" if is_path else "symbol"
        return (
            f"code_intel_test_for: no tests reference {kind} "
            f"{symbol_or_path!r}"
        )

    ranked = sorted(matches.items(), key=lambda kv: (-kv[1], kv[0]))[:cap]
    lines = [
        f"🧪 {len(matches)} test file(s) reference "
        f"{symbol_or_path!r}",
    ]
    for rel_str, count in ranked:
        lines.append(f"  {count:>3}× {rel_str}")
    if len(matches) > cap:
        lines.append(f"  … and {len(matches) - cap} more")

    return "\n".join(lines)


# Convenience export for agent-inventory wiring.
ALL_CODE_INTEL_TOOLS = (
    code_intel_find_symbol,
    code_intel_find_references,
    code_intel_find_callers,
    code_intel_type_check,
    code_intel_coverage,
    code_intel_deps,
    code_intel_history,
    code_intel_test_for,
)
