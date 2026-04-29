"""
app.affect.integrity — Affect-layer integrity manifest verifier.

Mirrors `app.subia.integrity` for the `app/affect/` tree. Separate
manifest at `app/affect/.integrity_manifest.json` so deploy-time
tampering of the welfare envelope, attachment caps, reference panel,
or hook handlers is caught independently of the SubIA manifest.

The same rationale that motivated the SubIA manifest applies here:
the Self-Improver agent CAN write code, so the runtime
`assert_not_self_improver(actor)` guard inside welfare.py is not
sufficient on its own — a code-writing path could rewrite the
HARD_ENVELOPE constants directly. This manifest catches that by
hash-comparison against a baseline shipped in git.

Usage (dev / CI / startup):

    from app.affect.integrity import (
        compute_manifest, write_manifest, load_manifest,
        verify_integrity,
    )

    # CI regen after an authorized change:
    write_manifest(compute_manifest())

    # Runtime verification:
    result = verify_integrity()
    if not result.ok:
        # missing files, hash mismatch, or no manifest at all
        ...

The module has no imports from anywhere inside `app/affect/` — if the
welfare envelope is broken, integrity verification must still run.

Infrastructure-level. Tier-3 protected.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# ── Manifest location (in-repo, ships with code) ──────────────────

# Repo root discovered relative to this file: app/affect/integrity.py
# parents[0] = app/affect
# parents[1] = app
# parents[2] = <repo-root>
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPO_ROOT / "app" / "affect" / ".integrity_manifest.json"
_DEFAULT_INCLUDE_ROOT = _REPO_ROOT / "app" / "affect"


@dataclass
class IntegrityResult:
    """Structured outcome of a verify_integrity call."""
    ok: bool = True
    manifest_version: int = 1
    n_files: int = 0
    missing: list = field(default_factory=list)       # declared, not on disk
    extra: list = field(default_factory=list)         # on disk, not declared
    mismatched: list = field(default_factory=list)    # declared, hash differs

    @property
    def has_drift(self) -> bool:
        return bool(self.missing or self.mismatched)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "manifest_version": self.manifest_version,
            "n_files": self.n_files,
            "missing": list(self.missing),
            "extra": list(self.extra),
            "mismatched": list(self.mismatched),
            "has_drift": self.has_drift,
        }


class IntegrityFault(RuntimeError):
    """Raised by verify_integrity(strict=True) when drift is detected."""


_MANIFEST_VERSION = 1

# File extensions covered by the manifest. The reference panel JSON is
# included on purpose — it's the fixed compass and tampering with it
# silences the wireheading defense.
_COVERED_SUFFIXES = (".py", ".json")
# Skip the manifest file itself + Python bytecode.
_SKIP_NAMES = {".integrity_manifest.json"}
_SKIP_DIRNAMES = {"__pycache__"}


def _iter_covered_files(root: Path) -> Iterable[Path]:
    """Yield every covered file under `root` deterministically."""
    if not root.exists():
        return
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(d in p.parts for d in _SKIP_DIRNAMES):
            continue
        if p.name in _SKIP_NAMES:
            continue
        if p.suffix not in _COVERED_SUFFIXES:
            continue
        files.append(p)
    for p in sorted(files):
        yield p


def _hash_file(path: Path) -> str:
    """SHA-256 hex digest of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_manifest(
    root: Path | str | None = None,
    repo_root: Path | str | None = None,
) -> dict:
    """Generate a manifest for every covered file under `root`.

    Returns a dict of shape:
        {
          "version": 1,
          "files": {
            "app/affect/<path>": {"sha256": "<hex>", "size": <int>},
            ...
          }
        }
    """
    repo = Path(repo_root) if repo_root else _REPO_ROOT
    tree = Path(root) if root else _DEFAULT_INCLUDE_ROOT

    entries: dict = {}
    for path in _iter_covered_files(tree):
        try:
            rel = str(path.relative_to(repo))
        except ValueError:
            continue
        digest = _hash_file(path)
        entries[rel] = {
            "sha256": digest,
            "size": path.stat().st_size,
        }

    return {
        "version": _MANIFEST_VERSION,
        "files": entries,
    }


def load_manifest(manifest_path: Path | str | None = None) -> dict | None:
    """Read the canonical manifest from disk. Returns None if absent."""
    path = Path(manifest_path) if manifest_path else _MANIFEST_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("affect integrity: failed to load manifest (%s)", exc)
        return None


def write_manifest(
    manifest: dict,
    manifest_path: Path | str | None = None,
) -> None:
    """Write the manifest atomically. Used by regeneration tooling
    (dev or CI), never at runtime from application code."""
    path = Path(manifest_path) if manifest_path else _MANIFEST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def verify_integrity(
    manifest: dict | None = None,
    repo_root: Path | str | None = None,
    strict: bool = False,
) -> IntegrityResult:
    """Compare live `app/affect/**` hashes to a manifest.

    Returns an IntegrityResult. Never silently succeeds in the
    absence of a manifest (the safety bug of older baseline schemes).
    """
    repo = Path(repo_root) if repo_root else _REPO_ROOT
    if manifest is None:
        manifest = load_manifest()

    if manifest is None:
        result = IntegrityResult(
            ok=False,
            manifest_version=0,
            missing=["<MANIFEST>"],
        )
        if strict:
            raise IntegrityFault(
                "Affect integrity manifest not found — refusing to run"
            )
        return result

    declared = manifest.get("files", {})
    result = IntegrityResult(
        manifest_version=int(manifest.get("version", 0)),
        n_files=len(declared),
    )

    for rel, entry in declared.items():
        full = repo / rel
        if not full.exists():
            result.missing.append(rel)
            continue
        live = _hash_file(full)
        expected = entry.get("sha256", "")
        if live != expected:
            result.mismatched.append({
                "file": rel,
                "expected": expected,
                "actual": live,
            })

    include_root = repo / "app" / "affect"
    if include_root.exists():
        live_files = set()
        for p in _iter_covered_files(include_root):
            try:
                rel = str(p.relative_to(repo))
            except ValueError:
                continue
            live_files.add(rel)
        for rel in live_files - set(declared):
            result.extra.append(rel)

    result.ok = not (result.missing or result.mismatched)

    if strict and not result.ok:
        raise IntegrityFault(
            f"Affect integrity drift: {len(result.missing)} missing, "
            f"{len(result.mismatched)} mismatched"
        )
    return result


__all__ = [
    "IntegrityResult",
    "IntegrityFault",
    "compute_manifest",
    "load_manifest",
    "write_manifest",
    "verify_integrity",
]
