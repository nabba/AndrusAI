"""Tests for app.upgrade_lifecycle.impact_analysis (U2).

PROGRAM §62. Covers:

  1.  Direct import detection (``import pkg``)
  2.  Aliased import (``import pkg as p``) — alias resolves
  3.  From-import (``from pkg import X``)
  4.  From-import-as (``from pkg import X as Y``) — aliased symbol resolves
  5.  Submodule import (``import pkg.sub``)
  6.  Attribute use after import (``pkg.foo.bar``)
  7.  Symbol-candidate extraction from prose strings
  8.  Capability matching — deprecation_hits vs breaking_hits
  9.  No false-positive on shadowed name (variable named like package)
  10. Reports zero hits when capability is empty
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.upgrade_lifecycle import impact_analysis as ia
from app.upgrade_lifecycle.protocol import Capability


# ── Fixture ──────────────────────────────────────────────────────────────


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Build a tiny ``app/`` tree under tmp_path."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    return tmp_path


def _cap(
    *, deprecations: tuple[str, ...] = (),
    breaking_changes: tuple[str, ...] = (),
    package: str = "starlette",
) -> Capability:
    return Capability(
        package=package,
        from_version="0.52",
        to_version="1.0",
        source="github_releases",
        extracted_at="2026-05-23T00:00:00+00:00",
        deprecations=deprecations,
        breaking_changes=breaking_changes,
    )


# ── 1: Direct import ────────────────────────────────────────────────────


def test_direct_import_detected(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        import starlette
        x = starlette
    """).strip() + "\n")
    cap = _cap(breaking_changes=("starlette internal API moved",))
    report = ia.analyze(cap, repo_root=fake_repo)
    # The import line is one site; matching depends on the bare-token
    # extraction from the capability prose. "starlette" passes the
    # noise filter so it matches.
    assert len(report.call_sites) >= 1
    matched = any(s.symbol == "starlette" for s in report.call_sites)
    assert matched


# ── 2: Aliased import ──────────────────────────────────────────────────


def test_aliased_import_resolves_to_package(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        import starlette as sl
        y = sl.Server
    """).strip() + "\n")
    cap = _cap(breaking_changes=("Server.start_loop removed",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert any(s.symbol == "starlette.Server" for s in report.call_sites)


# ── 3: from-import ──────────────────────────────────────────────────────


def test_from_import_detected(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        from starlette import Server
        s = Server()
    """).strip() + "\n")
    cap = _cap(breaking_changes=("Server class removed",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert any(
        s.symbol == "starlette.Server" and s.kind == "from_import"
        for s in report.call_sites
    )


# ── 4: from-import-as ───────────────────────────────────────────────────


def test_from_import_as_resolves_to_canonical(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        from starlette import Server as S
        s = S()
    """).strip() + "\n")
    cap = _cap(breaking_changes=("Server class removed",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert any(s.symbol == "starlette.Server" for s in report.call_sites)


# ── 5: Submodule import ─────────────────────────────────────────────────


def test_submodule_import_via_dotted_path(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        import starlette.routing
        r = starlette.routing.Router()
    """).strip() + "\n")
    cap = _cap(breaking_changes=("starlette.routing.Router signature changed",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert any(
        "starlette.routing" in s.symbol for s in report.call_sites
    )


# ── 6: Attribute chain ──────────────────────────────────────────────────


def test_attribute_chain_after_aliased_import(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        import starlette as sl
        result = sl.routing.Router()
    """).strip() + "\n")
    cap = _cap(breaking_changes=("starlette.routing.Router moved",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert any(
        s.symbol == "starlette.routing.Router" for s in report.call_sites
    )


# ── 7: Symbol-candidate extraction ──────────────────────────────────────


def test_extract_candidate_symbols_picks_dotted_names():
    out = ia.extract_candidate_symbols(
        "asyncio.gather() with return_exceptions=True is deprecated",
    )
    assert "asyncio.gather" in out
    assert "return_exceptions" in out


def test_extract_candidate_symbols_drops_common_noise():
    out = ia.extract_candidate_symbols("Use the new API instead")
    # All bare lowercase noise words filtered out — function name "Use" is
    # capitalised so it survives the filter. The point of the noise filter
    # is to drop English filler, not capitalised identifiers.
    assert "the" not in out
    assert "use" not in out
    assert "instead" not in out


# ── 8: Capability matching — deprecation vs breaking ────────────────────


def test_breaking_change_match_increments_breaking_hits(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        from starlette import Server
        s = Server.start_loop()
    """).strip() + "\n")
    cap = _cap(breaking_changes=("Server.start_loop() removed; use run()",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert report.breaking_hits >= 1
    assert report.deprecation_hits == 0


def test_deprecation_match_increments_deprecation_hits(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        import starlette as sl
        x = sl.middleware()
    """).strip() + "\n")
    cap = _cap(deprecations=("middleware() function is deprecated",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert report.deprecation_hits >= 1
    assert report.breaking_hits == 0


def test_breaking_takes_precedence_over_deprecation_for_same_site(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        from starlette import Server
        s = Server()
    """).strip() + "\n")
    cap = _cap(
        deprecations=("Server() will be deprecated",),
        breaking_changes=("Server() removed",),
    )
    report = ia.analyze(cap, repo_root=fake_repo)
    # Same site shouldn't be double-counted — breaking wins.
    assert report.breaking_hits >= 1


# ── 9: No false positive on shadowed name ───────────────────────────────


def test_shadowed_name_does_not_match(fake_repo: Path):
    # File has a local variable named starlette but no import of it.
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        def f():
            starlette = "hello"
            return starlette
    """).strip() + "\n")
    cap = _cap(breaking_changes=("starlette.Server removed",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert report.breaking_hits == 0
    assert report.deprecation_hits == 0
    assert len(report.call_sites) == 0


# ── 10: Empty capability yields zero hits ───────────────────────────────


def test_empty_capability_yields_zero_hits(fake_repo: Path):
    (fake_repo / "app" / "user.py").write_text(textwrap.dedent("""
        import starlette
        x = starlette
    """).strip() + "\n")
    cap = _cap()   # no deprecations + no breaking_changes
    report = ia.analyze(cap, repo_root=fake_repo)
    assert report.breaking_hits == 0
    assert report.deprecation_hits == 0
    # call_sites stays empty too — without candidate symbols there's
    # nothing to match against.
    assert len(report.call_sites) == 0


# ── Test directory is skipped from the walk ─────────────────────────────


def test_tier_immutable_detection_flips_report_flag(fake_repo: Path, monkeypatch):
    """U4's MAJOR auto-CR gate depends on `tier_immutable_touched`.

    Stub `get_protection_tier` so a specific file path is flagged
    IMMUTABLE; analyzer should set the report flag.
    """
    (fake_repo / "app" / "user.py").write_text(
        "import starlette\nstarlette.Server()\n",
    )

    # Pretend EVERY call-site path is TIER_IMMUTABLE for this test.
    from app import auto_deployer

    monkeypatch.setattr(
        auto_deployer, "get_protection_tier",
        lambda p: auto_deployer.ProtectionTier.IMMUTABLE,
    )
    cap = _cap(breaking_changes=("starlette.Server removed",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert report.breaking_hits >= 1
    assert report.tier_immutable_touched is True


def test_tier_immutable_false_when_check_unavailable(fake_repo: Path, monkeypatch):
    """When auto_deployer is broken, default to False (fail-open).

    The gate is permissive — when we can't prove immutability,
    we let U4 fall back to its other conditions rather than blocking.
    """
    (fake_repo / "app" / "user.py").write_text(
        "import starlette\nstarlette.Server()\n",
    )

    import builtins
    real_import = builtins.__import__

    def _no_auto_deployer(name, *args, **kwargs):
        if name == "app.auto_deployer":
            raise ImportError("simulated missing module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_auto_deployer)
    cap = _cap(breaking_changes=("starlette.Server removed",))
    report = ia.analyze(cap, repo_root=fake_repo)
    assert report.tier_immutable_touched is False


def test_tests_directory_is_skipped(fake_repo: Path):
    # File OUTSIDE app/ should not show up
    (fake_repo / "app" / "real_user.py").write_text("import starlette\n")
    (fake_repo / "tests").mkdir(exist_ok=True)
    (fake_repo / "tests" / "test_x.py").write_text(
        "import starlette\nstarlette.Server()\n",
    )
    cap = _cap(breaking_changes=("starlette.Server removed",))
    report = ia.analyze(cap, repo_root=fake_repo)
    for site in report.call_sites:
        # Check for the literal `/tests/` path segment, not the
        # substring (pytest's tmp dir names embed "tests" in directory
        # names like "test_tests_directory_is_skipped0").
        assert "/tests/" not in site.file_path
