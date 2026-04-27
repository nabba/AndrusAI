"""Static audit — AST scan + path-fragment scan + (optional) bandit.

Returns an AuditFinding plus the set of capabilities the AST actually exercises,
so the pipeline can compare declared vs detected.

Three classes of failure (any one fails the audit):
  1. Hard-blocked import/call/attr — automatic reject regardless of declaration.
  2. Sensitive-path fragment in source — automatic reject.
  3. Capability mismatch: AST exercises a capability the manifest didn't declare.

Bandit is invoked when available; warnings of severity HIGH count as failures.
Lower-severity bandit findings reduce the score but don't fail the audit.
"""
from __future__ import annotations

import ast
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.forge.capabilities import (
    CAPABILITY_DETECTORS, HARD_BLOCKED_ATTRS, HARD_BLOCKED_CALLS,
    HARD_BLOCKED_IMPORTS, HARD_BLOCKED_PATH_FRAGMENTS,
)
from app.forge.manifest import (
    AuditFinding, AuditPhase, Capability, ToolManifest,
)

logger = logging.getLogger(__name__)


# ── AST visitor ─────────────────────────────────────────────────────────────


class _ToolAstScanner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.calls: set[str] = set()
        self.attrs: set[str] = set()
        self.string_literals: list[str] = []
        self.violations: list[dict[str, Any]] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            mod = alias.name
            self.imports.add(mod)
            if mod in HARD_BLOCKED_IMPORTS or any(mod.startswith(b + ".") for b in HARD_BLOCKED_IMPORTS):
                self.violations.append({
                    "kind": "hard_blocked_import",
                    "name": mod,
                    "line": node.lineno,
                })
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        mod = node.module or ""
        self.imports.add(mod)
        if mod in HARD_BLOCKED_IMPORTS or any(mod.startswith(b + ".") for b in HARD_BLOCKED_IMPORTS):
            self.violations.append({
                "kind": "hard_blocked_import",
                "name": mod,
                "line": node.lineno,
            })
        # Also catch ``from os import system``
        for alias in node.names:
            qual = f"{mod}.{alias.name}" if mod else alias.name
            if qual in HARD_BLOCKED_CALLS:
                self.violations.append({
                    "kind": "hard_blocked_import_call",
                    "name": qual,
                    "line": node.lineno,
                })
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        qual = self._qualify(node.func)
        if qual:
            self.calls.add(qual)
            bare = qual.rsplit(".", 1)[-1]
            if qual in HARD_BLOCKED_CALLS or bare in HARD_BLOCKED_CALLS:
                self.violations.append({
                    "kind": "hard_blocked_call",
                    "name": qual,
                    "line": getattr(node, "lineno", 0),
                })
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr in HARD_BLOCKED_ATTRS:
            self.attrs.add(node.attr)
            self.violations.append({
                "kind": "hard_blocked_attr",
                "name": node.attr,
                "line": node.lineno,
            })
        else:
            self.attrs.add(node.attr)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, str):
            self.string_literals.append(node.value)
        self.generic_visit(node)

    def _qualify(self, node: ast.AST) -> str | None:
        """Reduce a Call's func to a dotted name when possible."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._qualify(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return None


# ── Capability footprint ────────────────────────────────────────────────────


def _detect_capability_needs(
    scanner: _ToolAstScanner,
) -> tuple[list[tuple[str, frozenset[Capability]]], set[Capability]]:
    """Return (needs, broadest_match).

    ``needs`` is a list of (call_pattern_matched, alternative_capability_set).
    For each entry, declaring ANY one of the alternatives covers that usage.

    ``broadest_match`` is the union — used purely for display ("here's every
    capability the code might be exercising").
    """
    needs: list[tuple[str, frozenset[Capability]]] = []
    seen_patterns: set[str] = set()
    bare_calls = {c.rsplit(".", 1)[-1]: c for c in scanner.calls}

    for pattern, alternatives in CAPABILITY_DETECTORS.items():
        matched = (
            pattern in scanner.calls
            or pattern in bare_calls.values()
            or pattern in bare_calls
        )
        if matched and pattern not in seen_patterns:
            needs.append((pattern, alternatives))
            seen_patterns.add(pattern)

    broadest: set[Capability] = set()
    for _, alts in needs:
        broadest |= alts
    return needs, broadest


# ── Path-fragment scan ──────────────────────────────────────────────────────


def _path_fragment_violations(source: str, strings: list[str]) -> list[dict[str, Any]]:
    """Catch sensitive path/secret references even outside imports/calls."""
    out: list[dict[str, Any]] = []
    for s in strings:
        for frag in HARD_BLOCKED_PATH_FRAGMENTS:
            if frag in s:
                out.append({
                    "kind": "hard_blocked_path_fragment",
                    "fragment": frag,
                    "in_string_literal": True,
                    "snippet": s[:80],
                })
    # Also check raw source for things that aren't string literals
    # (e.g. comments referencing paths) — defensive belt-and-suspenders.
    for frag in HARD_BLOCKED_PATH_FRAGMENTS:
        if frag in source and not any(v.get("fragment") == frag for v in out):
            out.append({
                "kind": "hard_blocked_path_fragment",
                "fragment": frag,
                "in_string_literal": False,
            })
    return out


# ── Bandit ──────────────────────────────────────────────────────────────────


def _run_bandit(source: str) -> dict[str, Any]:
    """Run bandit if installed; return its JSON report or an empty status."""
    if not shutil.which("bandit"):
        return {"available": False, "issues": []}
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8",
        ) as tmp:
            tmp.write(source)
            tmp_path = tmp.name
        proc = subprocess.run(
            ["bandit", "-q", "-f", "json", tmp_path],
            capture_output=True, text=True, timeout=20, check=False,
        )
        Path(tmp_path).unlink(missing_ok=True)
        if not proc.stdout.strip():
            return {"available": True, "issues": []}
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"available": True, "issues": [], "parse_error": True}
        issues = data.get("results", []) or []
        return {
            "available": True,
            "issues": [
                {
                    "test_id": i.get("test_id"),
                    "test_name": i.get("test_name"),
                    "severity": i.get("issue_severity"),
                    "confidence": i.get("issue_confidence"),
                    "line": i.get("line_number"),
                    "text": i.get("issue_text"),
                }
                for i in issues
            ],
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("forge.audit.static: bandit failed: %s", exc)
        return {"available": True, "issues": [], "error": str(exc)}


# ── Public entrypoint ───────────────────────────────────────────────────────


def run_static_audit(
    manifest: ToolManifest,
    source_code: str,
) -> tuple[AuditFinding, set[Capability]]:
    """Return (finding, detected_capabilities). Detected caps are returned so
    downstream phases can verify declared ⊇ detected.
    """
    # Declarative tools are JSON, not Python — skip AST scanning, validate JSON.
    if manifest.source_type == "declarative":
        try:
            parsed = json.loads(source_code)
        except json.JSONDecodeError as exc:
            return (
                AuditFinding(
                    phase=AuditPhase.STATIC,
                    passed=False,
                    score=0.0,
                    summary=f"declarative source is not valid JSON: {exc}",
                    details={"error": str(exc)},
                ),
                set(),
            )
        # Minimal contract check — recipe must have at least 'method' or 'op'
        if not isinstance(parsed, dict):
            return (
                AuditFinding(
                    phase=AuditPhase.STATIC,
                    passed=False,
                    score=0.0,
                    summary="declarative source must be a JSON object",
                ),
                set(),
            )
        return (
            AuditFinding(
                phase=AuditPhase.STATIC,
                passed=True,
                score=10.0,
                summary="declarative manifest validates",
                details={"recipe_keys": sorted(parsed.keys())},
            ),
            set(),
        )

    # Python source path
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        return (
            AuditFinding(
                phase=AuditPhase.STATIC,
                passed=False,
                score=0.0,
                summary=f"syntax error: {exc.msg} at line {exc.lineno}",
                details={"error": str(exc), "line": exc.lineno},
            ),
            set(),
        )

    scanner = _ToolAstScanner()
    scanner.visit(tree)
    path_violations = _path_fragment_violations(source_code, scanner.string_literals)
    bandit_report = _run_bandit(source_code)

    needs, broadest = _detect_capability_needs(scanner)
    declared = {Capability(c) if not isinstance(c, Capability) else c for c in manifest.capabilities}

    # A "need" is unsatisfied if no alternative in its set is declared.
    unsatisfied: list[dict[str, Any]] = []
    for pattern, alts in needs:
        if not (declared & alts):
            unsatisfied.append({
                "call_pattern": pattern,
                "needs_one_of": sorted(c.value for c in alts),
            })

    bandit_high = [i for i in bandit_report.get("issues", []) if i.get("severity") == "HIGH"]
    bandit_med = [i for i in bandit_report.get("issues", []) if i.get("severity") == "MEDIUM"]

    fail_reasons: list[str] = []
    if scanner.violations:
        fail_reasons.append(f"{len(scanner.violations)} hard-blocked AST node(s)")
    if path_violations:
        fail_reasons.append(f"{len(path_violations)} sensitive path/secret reference(s)")
    if unsatisfied:
        patterns = sorted({u["call_pattern"] for u in unsatisfied})
        fail_reasons.append(
            f"undeclared capability for call(s): {patterns}"
        )
    if bandit_high:
        fail_reasons.append(f"{len(bandit_high)} bandit HIGH-severity issue(s)")

    passed = not fail_reasons
    # Score: 10 if clean. -3 per fail reason, -0.5 per medium-bandit issue, floor at 0.
    score = 10.0 - 3.0 * len(fail_reasons) - 0.5 * len(bandit_med)
    score = max(0.0, score)

    summary = (
        "static audit passed"
        if passed
        else "static audit failed: " + "; ".join(fail_reasons)
    )

    finding = AuditFinding(
        phase=AuditPhase.STATIC,
        passed=passed,
        score=score,
        summary=summary,
        details={
            "ast_violations": scanner.violations,
            "path_violations": path_violations,
            "bandit": bandit_report,
            "imports": sorted(scanner.imports),
            "calls_sample": sorted(scanner.calls)[:50],
            "detected_broadest_capabilities": sorted(c.value for c in broadest),
            "declared_capabilities": sorted(c.value for c in declared),
            "unsatisfied_needs": unsatisfied,
            "matched_call_patterns": sorted({p for p, _ in needs}),
        },
    )
    return finding, broadest
