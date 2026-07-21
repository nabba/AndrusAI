"""Regression coverage for the affect/welfare integrity boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.affect.integrity import (
    IntegrityFault,
    compute_manifest,
    load_manifest,
    verify_integrity,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_committed_affect_manifest_matches_live_tree() -> None:
    committed = load_manifest()
    fresh = compute_manifest()

    assert committed is not None
    assert committed == fresh
    assert verify_integrity().ok


def test_affect_integrity_fails_on_undeclared_file() -> None:
    manifest = compute_manifest()
    rel = next(iter(manifest["files"]))
    manifest["files"] = dict(manifest["files"])
    manifest["files"].pop(rel)

    result = verify_integrity(manifest=manifest, repo_root=REPO_ROOT)

    assert not result.ok
    assert result.has_drift
    assert rel in result.extra


def test_affect_integrity_strict_mode_rejects_undeclared_file() -> None:
    manifest = compute_manifest()
    rel = next(iter(manifest["files"]))
    manifest["files"] = dict(manifest["files"])
    manifest["files"].pop(rel)

    with pytest.raises(IntegrityFault, match="extra"):
        verify_integrity(
            manifest=manifest,
            repo_root=REPO_ROOT,
            strict=True,
        )
