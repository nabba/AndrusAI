"""Grounded change-spec builder for the verified mutation engine (2026-05-27).

Purpose
───────
Before a self-modification is attempted, assemble a truthful, *truncation-free*
contract for the target file: its public API surface, who depends on it, the
tests that guard it, its imports, and a set of executable preservation
assertions the change must keep green.

This is the direct antidote to the old AVO implementer's 8000-char blind read
(``app/avo_operator.py:257`` — ``content[:8000]``), which produced canonical
framework scaffolds because it never *saw* — and therefore silently discarded —
the real file's public API (e.g. ``ResearchCrew.run``, the entry point every
caller depends on).

Tier
────
GENERATION-side helper (OPEN tier). It *informs* the implementer; it does not
judge. The judgement (running the changed code against a held-out benchmark)
lives in the TIER_IMMUTABLE ``worktree_eval`` module so the Self-Improver can
never lower its own bar.

The query layer is ``app.code_intel`` (pure-stdlib AST index) — no truncation
anywhere, robust against the embedding/LLM rotations that plague vector stores.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.code_intel import (
    SymbolKind,
    SymbolLocation,
    build_index,
    find_callers,
    find_module_deps,
    find_test_coverage,
    is_built,
    load_index,
    save_index,
)

logger = logging.getLogger(__name__)

# Cap on reported external callers per symbol — keeps the prompt block bounded
# on hot-path files with hundreds of references.
_BLAST_RADIUS_LIMIT = 50


def _default_root() -> Path:
    """Repo root, agreeing with the live code_intel index.

    Honours ``CODE_INTEL_ROOT`` (the same override the code_intel refresh job
    uses) so spec-building and the live index target the same tree. Falls back
    to the checkout root inferred from this file's location — which resolves
    correctly both in-container (``/app``) and on a host dev checkout.
    """
    env = os.environ.get("CODE_INTEL_ROOT")
    if env:
        return Path(env)
    # app/self_improvement/change_spec.py → parents[2] is the repo/container root.
    return Path(__file__).resolve().parents[2]


def _rel(target_file: str | Path, root: Path) -> str:
    """Normalise ``target_file`` to a repo-relative POSIX path.

    The code_intel index stores file paths relative to the root it was built
    over, so a lookup must use the same relative form.
    """
    p = Path(target_file)
    if p.is_absolute():
        try:
            return p.relative_to(root).as_posix()
        except ValueError:
            return p.as_posix()
    return p.as_posix()


def _module_path(rel_path: str) -> str:
    """``app/crews/research_crew.py`` → ``app.crews.research_crew``."""
    stem = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    return stem.replace("/", ".")


def _is_public(s: SymbolLocation) -> bool:
    """A symbol is part of the preserved contract when it's a top-level
    function/class (no leading underscore) or a public method of a public
    class. Private (``_``-prefixed) symbols are implementation detail."""
    if s.name.startswith("_"):
        return False
    if s.kind is SymbolKind.CLASS:
        return s.parent == ""
    if s.kind in (SymbolKind.FUNCTION, SymbolKind.ASYNC_FUNCTION):
        return s.parent == ""  # top-level only — not nested helpers
    if s.kind in (SymbolKind.METHOD, SymbolKind.ASYNC_METHOD):
        return bool(s.parent) and not s.parent.startswith("_")
    return False


def _build_preservation_assertions(
    module_path: str, public_symbols: list[SymbolLocation]
) -> list[str]:
    """Generate executable assertions that pin the public API.

    These run as a synthesized smoke-test in the implementer's worktree. A
    framework scaffold that drops ``run`` or references a missing config file
    fails the ``import`` line or a ``hasattr`` line immediately — which is
    exactly the breakage the old pipeline shipped because nothing executed it.
    """
    lines = [f"import {module_path} as _m"]
    classes = {s.name for s in public_symbols if s.kind is SymbolKind.CLASS}
    for s in public_symbols:
        if s.kind is SymbolKind.CLASS:
            lines.append(
                f"assert hasattr(_m, {s.name!r}), 'public class {s.name} removed'"
            )
        elif s.kind in (SymbolKind.FUNCTION, SymbolKind.ASYNC_FUNCTION):
            lines.append(
                f"assert hasattr(_m, {s.name!r}), 'public function {s.name} removed'"
            )
        elif s.kind in (SymbolKind.METHOD, SymbolKind.ASYNC_METHOD):
            if s.parent in classes:
                lines.append(
                    f"assert hasattr(getattr(_m, {s.parent!r}), {s.name!r}), "
                    f"'method {s.parent}.{s.name} removed'"
                )
    return lines


@dataclass
class ChangeSpec:
    """A truncation-free contract for a single target file.

    ``full_source`` is the COMPLETE file — never truncated. ``callers`` maps a
    fully-qualified public symbol to the external ``file:caller`` sites that
    depend on it (the blast radius). ``preservation_assertions`` are executable
    Python lines the change must keep true.
    """

    target_file: str  # repo-relative POSIX
    module_path: str  # dotted, e.g. app.crews.research_crew
    full_source: str
    public_symbols: list[SymbolLocation] = field(default_factory=list)
    callers: dict[str, list[str]] = field(default_factory=dict)
    covering_tests: list[str] = field(default_factory=list)
    module_deps: list[str] = field(default_factory=list)
    preservation_assertions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def caller_count(self) -> int:
        return sum(len(v) for v in self.callers.values())

    @property
    def is_grounded(self) -> bool:
        """True when we have the source AND at least one indexed public symbol —
        i.e. the implementer is working from reality, not a blank slate."""
        return bool(self.full_source) and bool(self.public_symbols)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_file": self.target_file,
            "module_path": self.module_path,
            "source_chars": len(self.full_source),
            "public_symbols": [s.fully_qualified for s in self.public_symbols],
            "caller_count": self.caller_count,
            "covering_tests": self.covering_tests,
            "module_deps": self.module_deps,
            "preservation_assertions": self.preservation_assertions,
            "notes": self.notes,
        }


def _ensure_index(root: Path, *, refresh: bool = False) -> None:
    """Build the code_intel index over ``root`` if missing (or when ``refresh``).

    Failure-isolated: a build error logs a warning and leaves whatever index
    exists — ``build_change_spec`` then degrades to a source-only spec rather
    than raising.
    """
    try:
        if refresh or not is_built():
            snapshot = build_index(root=root)
            save_index(snapshot)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("change_spec: index build failed for %s: %s", root, exc)


def build_change_spec(
    target_file: str | Path,
    *,
    root: Optional[Path | str] = None,
    ensure_index: bool = True,
    refresh: bool = False,
) -> ChangeSpec:
    """Assemble the grounded contract for ``target_file``.

    Parameters
    ----------
    target_file
        Repo-relative or absolute path to the ``.py`` file to be changed.
    root
        Repo root. Defaults to ``CODE_INTEL_ROOT`` or the inferred checkout root.
    ensure_index
        Build the index if it doesn't exist yet (default True).
    refresh
        Force a full index rebuild before querying — use this from the idle
        loop so the spec reflects the current tree, not a stale snapshot.
    """
    root_path = Path(root) if root else _default_root()
    rel = _rel(target_file, root_path)
    abs_path = root_path / rel

    full_source = ""
    try:
        full_source = abs_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("change_spec: cannot read %s: %s", abs_path, exc)

    if ensure_index:
        _ensure_index(root_path, refresh=refresh)

    snapshot = load_index()
    in_file = [s for s in snapshot.symbols if s.file_path == rel]
    public_symbols = sorted(
        (s for s in in_file if _is_public(s)), key=lambda s: (s.lineno, s.name)
    )

    # Blast radius — external callers of each public symbol (breaking these
    # breaks the system). Self-references inside the target file are excluded.
    callers: dict[str, list[str]] = {}
    for s in public_symbols:
        sites = find_callers(s.name, limit=_BLAST_RADIUS_LIMIT)
        external = sorted(
            {f"{c.file_path}:{c.fully_qualified}" for c in sites if c.file_path != rel}
        )
        if external:
            callers[s.fully_qualified] = external

    # Covering tests — test files that reference any public symbol by name.
    tests: set[str] = set()
    for s in public_symbols:
        for ref in find_test_coverage(s.name):
            tests.add(ref.file_path)
    covering_tests = sorted(tests)

    module_deps = find_module_deps(str(abs_path))
    module_path = _module_path(rel)
    preservation_assertions = _build_preservation_assertions(module_path, public_symbols)

    notes: list[str] = []
    if not full_source:
        notes.append("WARNING: target source unreadable — spec is partial")
    if not public_symbols:
        notes.append(
            "No indexed public symbols (index stale, file new, or no public API)"
        )
    if not covering_tests:
        notes.append(
            "No covering tests found — implementer MUST synthesize a smoke test"
        )

    return ChangeSpec(
        target_file=rel,
        module_path=module_path,
        full_source=full_source,
        public_symbols=public_symbols,
        callers=callers,
        covering_tests=covering_tests,
        module_deps=module_deps,
        preservation_assertions=preservation_assertions,
        notes=notes,
    )


def render_for_prompt(spec: ChangeSpec) -> str:
    """Render the grounded contract as a prompt block for the implementer LLM.

    Unlike the old implementer (which fed an 8 KB fragment and said "rewrite the
    complete file"), this hands the model the WHOLE file plus the contract it
    must honour — so a focused edit is the path of least resistance, not a
    from-scratch regeneration.
    """
    out: list[str] = [f"## GROUNDED CONTRACT for `{spec.target_file}`", ""]

    if spec.public_symbols:
        out.append("### Public API that MUST be preserved (callers depend on these):")
        for s in spec.public_symbols:
            doc = f" — {s.docstring_first_line}" if s.docstring_first_line else ""
            out.append(f"  - {s.kind.value} `{s.fully_qualified}` (line {s.lineno}){doc}")
        out.append("")

    if spec.callers:
        out.append("### Blast radius — external callers (breaking these breaks the system):")
        for fq, sites in sorted(spec.callers.items()):
            shown = ", ".join(sites[:8]) + (" …" if len(sites) > 8 else "")
            out.append(f"  - `{fq}` ← {shown}")
        out.append("")

    if spec.covering_tests:
        out.append("### Tests that guard this file (must stay green after your change):")
        out.extend(f"  - {t}" for t in spec.covering_tests)
        out.append("")

    if spec.preservation_assertions:
        out.append("### Preservation assertions (your change MUST keep these true):")
        out.append("```python")
        out.extend(spec.preservation_assertions)
        out.append("```")
        out.append("")

    if spec.notes:
        out.append("### Notes:")
        out.extend(f"  - {n}" for n in spec.notes)
        out.append("")

    out.append("### COMPLETE current file — MODIFY this, do NOT rewrite from scratch:")
    out.append(f"```python\n{spec.full_source}\n```")
    return "\n".join(out)
