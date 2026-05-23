"""U2 — Impact analysis.

PROGRAM §62 — Stage C of the upgrade lifecycle. Given a
:class:`~app.upgrade_lifecycle.protocol.Capability` produced by U1,
walks the codebase looking for files that import (or use) the
upgrading package and matches every reference against the
capability's ``deprecations`` and ``breaking_changes`` lists.

Output is an :class:`~app.upgrade_lifecycle.protocol.ImpactReport` —
the data structure U4's MAJOR auto-CR gate consults:

  * ``breaking_hits == 0`` — clean to auto-CR
  * ``tier_immutable_touched`` — refused regardless of bump severity
  * ``call_sites`` — surfaced in the CR body so the operator can
    eyeball each affected file

Cheap, deterministic, no LLM. ~2 s for a full repo walk on a warm
filesystem.
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Iterable, Optional

from app.upgrade_lifecycle.protocol import CallSite, Capability, ImpactReport

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────


# Symbol-extraction regex — pulls dotted Python identifiers out of free-text
# capability strings. Used to derive what to grep for in the AST walk.
# Examples:
#   "asyncio.gather() with return_exceptions=True"
#     -> {"asyncio.gather", "return_exceptions"}
#   "Removed legacy Server.start_loop(); use run()"
#     -> {"Server.start_loop", "run"}
#
# We deliberately allow single-name symbols (``run``, ``replace``) so a
# capability string like "use replace() instead" still produces a token.
# False-positives are bounded by the AST walk requiring the symbol to
# appear in a context that ALSO references the upgrading package.
_SYMBOL_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b",
)

# Strings we definitely don't want as candidate symbols.
_COMMON_NOISE = frozenset({
    "the", "and", "for", "with", "not", "use", "instead", "version",
    "removed", "added", "changed", "deprecated", "support", "supports",
    "now", "new", "old", "fix", "fixes", "fixed", "true", "false",
    "none", "see", "via", "from", "into", "this", "that", "be", "is",
    "are", "as", "to", "of", "in", "on", "at", "by", "or",
    "release", "released", "patch", "minor", "major",
})


# ── Symbol extraction ────────────────────────────────────────────────────


def extract_candidate_symbols(text: str) -> set[str]:
    """Return the set of plausible Python-symbol tokens in *text*.

    For dotted names, emits BOTH the full dotted form and each
    constituent identifier as a separate candidate. This is what
    lets a capability string "Server.start_loop removed" match a
    call site whose symbol is just ``Server`` (via
    ``from pkg import Server``).

    Common English words filter via :data:`_COMMON_NOISE` so prose
    fillers don't manufacture false positives.
    """
    out: set[str] = set()
    if not text:
        return out
    for m in _SYMBOL_RE.finditer(text):
        token = m.group(0)
        if "." in token:
            out.add(token)
            # Also emit the individual segments so a deprecation referring
            # to ``Server.start_loop`` matches ``Server`` and ``start_loop``
            # too. Each segment still passes through the noise filter so
            # bare lowercase fillers ("the", "from") don't sneak in.
            for piece in token.split("."):
                if piece.lower() not in _COMMON_NOISE:
                    out.add(piece)
            continue
        if token.lower() in _COMMON_NOISE:
            continue
        out.add(token)
    return out


def candidate_symbols_for(cap: Capability) -> dict[str, set[str]]:
    """Return ``{"deprecation": {...}, "breaking": {...}}``."""
    return {
        "deprecation": _union_strings(cap.deprecations),
        "breaking": _union_strings(cap.breaking_changes),
    }


def _union_strings(strings: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for s in strings:
        out |= extract_candidate_symbols(s)
    return out


# ── AST walk ─────────────────────────────────────────────────────────────


def _is_under_package(name: str, package: str) -> bool:
    """Match ``pkg`` and ``pkg.sub`` against importable name *name*.

    Mirrors how ``ast.Import.name`` and ``ast.ImportFrom.module`` store
    dotted paths: ``import pkg.sub`` produces ``name='pkg.sub'``.
    """
    if name == package:
        return True
    if name.startswith(f"{package}."):
        return True
    return False


def _resolve_module_name(*, level: int, module: Optional[str], file_path: Path,
                        repo_root: Path) -> Optional[str]:
    """Resolve ``from .relative import X`` against the importing file.

    Returns the absolute dotted module name, or None if it can't be
    resolved cleanly. Only used so an internal ``from .submod import X``
    inside the upgrading package itself is identified correctly when
    the package IS the codebase. For third-party packages the level=0
    branch returns ``module`` verbatim.
    """
    if level == 0:
        return module
    try:
        rel = file_path.relative_to(repo_root)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    # Walk up `level` segments from the file's package.
    # `from . import x` inside foo/bar/baz.py with level=1 walks to foo/bar.
    parent = parts[:-level] if level <= len(parts) else []
    if module:
        parent.append(module)
    return ".".join(parent) if parent else None


def _collect_imports(tree: ast.AST, file_path: Path, repo_root: Path,
                    *, package: str) -> tuple[dict[str, str], list[CallSite]]:
    """Walk *tree* for imports of *package*.

    Returns ``(alias_map, sites)`` where:

      * ``alias_map`` maps the local binding -> fully-qualified prefix
        in the upgrading package (so attribute uses can be resolved).
      * ``sites`` is the list of import-line :class:`CallSite` records.
    """
    alias_map: dict[str, str] = {}
    sites: list[CallSite] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if _is_under_package(name, package):
                    bound = alias.asname or name.split(".")[0]
                    alias_map[bound] = name
                    sites.append(CallSite(
                        file_path=str(file_path),
                        line=node.lineno,
                        symbol=name,
                        kind="import",
                    ))
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_module_name(
                level=node.level or 0, module=node.module,
                file_path=file_path, repo_root=repo_root,
            )
            if resolved is None or not _is_under_package(resolved, package):
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                # ``from pkg.sub import X as Y`` → Y bound to pkg.sub.X
                qual = f"{resolved}.{alias.name}" if alias.name != "*" else resolved
                alias_map[local] = qual
                sites.append(CallSite(
                    file_path=str(file_path),
                    line=node.lineno,
                    symbol=qual,
                    kind="from_import",
                ))
    return alias_map, sites


def _flatten_attribute(node: ast.AST) -> Optional[str]:
    """Flatten ``a.b.c`` ``Attribute`` chain to ``"a.b.c"``.

    Returns None if the root isn't an ``ast.Name`` (e.g. method on a
    function-call return — outside our static analysis scope).
    """
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _collect_attribute_uses(tree: ast.AST, file_path: Path,
                            alias_map: dict[str, str]) -> list[CallSite]:
    """Find attribute-chain uses of any binding in *alias_map*."""
    out: list[CallSite] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            flat = _flatten_attribute(node)
            if not flat:
                continue
            head = flat.split(".", 1)[0]
            if head not in alias_map:
                continue
            qual_prefix = alias_map[head]
            rest = flat[len(head):]
            resolved = qual_prefix + rest
            out.append(CallSite(
                file_path=str(file_path),
                line=node.lineno,
                symbol=resolved,
                kind="attribute",
            ))
    return out


# ── Public API ───────────────────────────────────────────────────────────


def _iter_python_files(repo_root: Path,
                       *, search_subdirs: tuple[str, ...] = ("app",)) -> Iterable[Path]:
    """Walk *repo_root* yielding .py files under each requested subdir.

    Skips ``__pycache__`` and any file under ``tests/`` — tests
    import a lot of third-party packages that aren't part of our
    production graph and would skew the impact counts.
    """
    for sub in search_subdirs:
        base = repo_root / sub
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            parts = set(p.parts)
            if "__pycache__" in parts or "tests" in parts:
                continue
            yield p


def _check_tier_immutable(call_sites: Iterable[CallSite]) -> bool:
    """True iff any call-site path is in TIER_IMMUTABLE.

    Failure-isolated: when ``auto_deployer`` isn't importable (stripped
    test env), defaults to False so the analyzer remains usable in
    isolation tests. Production always has the module.
    """
    try:
        from app.auto_deployer import ProtectionTier, get_protection_tier
    except Exception:
        return False
    for site in call_sites:
        rel = site.file_path
        # Normalise — get_protection_tier expects the path under repo root.
        # We pass call_sites with absolute paths; the tier checker normalises
        # internally (strips leading slashes) but it expects repo-relative
        # form. Try both: absolute first, then a few sensible prefixes.
        for candidate in (rel, _strip_repo_prefix(rel)):
            try:
                if get_protection_tier(candidate) == ProtectionTier.IMMUTABLE:
                    return True
            except Exception:
                continue
    return False


def _strip_repo_prefix(p: str) -> str:
    """Best-effort conversion of an absolute path to repo-relative form."""
    marker = "/crewai-team/"
    idx = p.find(marker)
    if idx >= 0:
        return p[idx + len(marker):]
    marker2 = "/BotArmy/crewai-team/"
    idx = p.find(marker2)
    if idx >= 0:
        return p[idx + len(marker2):]
    return p


def _match_against_capability(
    site: CallSite,
    *,
    deprecation_symbols: set[str],
    breaking_symbols: set[str],
) -> tuple[bool, bool, str]:
    """Return ``(is_deprecation, is_breaking, matched)``.

    Matching rule: the call-site's symbol (or any of its dotted suffix
    components) appears in the candidate symbol set. The dotted-suffix
    sweep matters because ``asyncio.gather`` extracted from a capability
    string should match a call site whose symbol is just ``gather`` (via
    ``from asyncio import gather``).
    """
    candidates = {site.symbol}
    parts = site.symbol.split(".")
    for i in range(1, len(parts)):
        candidates.add(".".join(parts[i:]))
    candidates.add(parts[-1])

    matched_breaking = candidates & breaking_symbols
    matched_deprec = candidates & deprecation_symbols
    if matched_breaking:
        return False, True, next(iter(matched_breaking))
    if matched_deprec:
        return True, False, next(iter(matched_deprec))
    return False, False, ""


def analyze(
    capability: Capability,
    *,
    repo_root: Optional[Path] = None,
    search_subdirs: tuple[str, ...] = ("app",),
) -> ImpactReport:
    """Walk the codebase, match against *capability*, return :class:`ImpactReport`.

    *repo_root* defaults to the parent of ``app/`` discovered via
    ``app.paths``; tests override it to point at a fixture tree.
    """
    if repo_root is None:
        try:
            from app.paths import WORKSPACE_ROOT
            # WORKSPACE_ROOT/.. is the deploy bundle; app/ lives at deploy/.
            # The factual repo root is the parent of ``app/`` — look at
            # __file__'s ancestry instead.
            repo_root = Path(__file__).resolve().parents[2]
        except Exception:
            repo_root = Path.cwd()

    cap_symbols = candidate_symbols_for(capability)
    deprec_syms, breaking_syms = cap_symbols["deprecation"], cap_symbols["breaking"]

    report = ImpactReport(
        package=capability.package,
        from_version=capability.from_version,
        to_version=capability.to_version,
    )

    package_dotted = capability.package.replace("-", "_")

    for py_file in _iter_python_files(repo_root, search_subdirs=search_subdirs):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError):
            continue
        alias_map, import_sites = _collect_imports(
            tree, py_file, repo_root, package=package_dotted,
        )
        if not alias_map:
            continue
        attr_sites = _collect_attribute_uses(tree, py_file, alias_map)
        all_sites = list(import_sites) + attr_sites
        for site in all_sites:
            is_deprec, is_break, matched = _match_against_capability(
                site,
                deprecation_symbols=deprec_syms,
                breaking_symbols=breaking_syms,
            )
            if is_deprec or is_break:
                report.call_sites.append(CallSite(
                    file_path=site.file_path,
                    line=site.line,
                    symbol=site.symbol,
                    kind=site.kind,
                    matched_capability=matched,
                ))
                if is_break:
                    report.breaking_hits += 1
                else:
                    report.deprecation_hits += 1

    report.tier_immutable_touched = _check_tier_immutable(report.call_sites)
    return report
