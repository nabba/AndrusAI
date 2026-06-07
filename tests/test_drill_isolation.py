"""Drill isolation pinning tests.

Pins the safety guards that prevent the 2026-05-16 corruption
incident from recurring in restore-drill.sh and version-upgrade-drill.sh.
The sibling migration-drill.sh has its own pinning tests in
test_q13_resilience_year2.py — those guards are functionally identical
but use a different overlay file (narrower scope: postgres only).

The flaw being guarded against: `docker compose -p <project>` renames
containers and networks but reads bind-mount paths literally from
docker-compose.yml. Without an overlay, both the live stack and the
drill stack mount ./workspace/mem0_pgdata (and mem0_neo4j), race on the
data-dir lock, and the drill's partial restore corrupts the live
databases.

ChromaDB is intentionally absent from both drills as of PROGRAM §55
(2026-05-17): it is no longer a compose service (it runs only as an
embedded library inside the gateway), so the drills no longer bring up
or restore a chromadb container. ChromaDB resilience is drilled by the
``source_ledger_replay`` + ``embedding_rotation`` resilience drills
instead. ``_assert_no_chromadb_leg`` below pins that removal so a future
edit can't silently reintroduce the broken leg.
"""
from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _drill_script(name: str) -> Path:
    p = REPO_ROOT / "deploy" / "scripts" / name
    assert p.is_file(), f"missing drill script: {p}"
    return p


def test_drill_isolation_overlay_present() -> None:
    """The shared overlay must exist and remap both data services
    (postgres + neo4j) to ephemeral named volumes."""
    p = REPO_ROOT / "docker-compose.drill-isolation.yml"
    assert p.is_file(), (
        "docker-compose.drill-isolation.yml must exist alongside "
        "docker-compose.yml — both restore-drill.sh and "
        "version-upgrade-drill.sh reference it explicitly."
    )
    src = p.read_text()
    # Must define one named volume per data service.
    for vol in ("drill_pgdata", "drill_neo4j"):
        assert vol in src, (
            f"Overlay must define a drill-specific volume {vol}."
        )
    # Must use !override on volumes (not !reset). !reset clears the
    # parent list entirely — the drill container would start with NO
    # data dir and initdb would fail in confusing ways.
    assert "!override" in src, (
        "Overlay must use !override on volumes (not !reset — that "
        "erases the list instead of replacing it)."
    )
    # Must NOT reference the live bind-mount paths in any volume mount
    # context. (Header comment can describe them; the assertion below
    # focuses on what's load-bearing.)
    # The live mount sources are sufficiently unique substrings that
    # a regex-free check is fine here.
    body_after_services = src.split("services:", 1)[-1] if "services:" in src else src
    # Find the volumes: section under services (not the top-level one).
    # We just check that no live path appears in any volume line that
    # would actually mount into a container.
    for live_path in (
        "./workspace/mem0_pgdata",
        "./workspace/mem0_neo4j",
    ):
        assert live_path not in body_after_services, (
            f"Overlay must not reference the live bind-mount {live_path}."
        )
    # The overlay must NOT declare a chromadb service. A volumes-only
    # service block (no image / no build) makes the merged
    # `docker compose config` invalid — that's the exact bug the §55
    # cleanup left behind and this test guards against.
    assert "chromadb:" not in body_after_services, (
        "Overlay must not declare a chromadb service — chromadb is no "
        "longer a compose service (PROGRAM §55). A volumes-only block "
        "breaks `docker compose config`."
    )


def _assert_drill_uses_isolation(script: Path) -> None:
    src = script.read_text()
    # Must load the overlay file. The exact -f flag arrangement is up
    # to the script, but the overlay filename must appear.
    assert "docker-compose.drill-isolation.yml" in src, (
        f"{script.name} must load docker-compose.drill-isolation.yml. "
        "Without it the drill corrupts the live databases."
    )
    # Pre-flight check for both live data containers. The drill brings
    # up both services, so both live counterparts can race.
    for live in (
        "crewai-team-postgres-1",
        "crewai-team-neo4j-1",
    ):
        assert live in src, (
            f"{script.name} must pre-flight-check for {live} before "
            "starting its own stack."
        )


def test_restore_drill_uses_isolation() -> None:
    _assert_drill_uses_isolation(_drill_script("restore-drill.sh"))


def test_version_upgrade_drill_uses_isolation() -> None:
    _assert_drill_uses_isolation(_drill_script("version-upgrade-drill.sh"))


def test_restore_drill_executable() -> None:
    p = _drill_script("restore-drill.sh")
    assert os.access(p, os.X_OK), f"{p.name} must be executable"


def test_version_upgrade_drill_executable() -> None:
    p = _drill_script("version-upgrade-drill.sh")
    assert os.access(p, os.X_OK), f"{p.name} must be executable"


# ─────────────────────────────────────────────────────────────────────────
# Follow-up fixes (PR splitting out the two pre-existing bugs noted in
# the sibling-isolation PR body).
# ─────────────────────────────────────────────────────────────────────────


def test_compose_image_tags_are_overrideable() -> None:
    """docker-compose.yml must use ${IMAGE:-default} placeholders for
    the data services, so version-upgrade-drill.sh's env-var image
    overrides actually take effect. The default values must preserve
    the previous hardcoded tags so live-stack behaviour is unchanged
    unless the operator explicitly sets the env vars.

    The chromadb service was deliberately removed from
    docker-compose.yml per PROGRAM §55 (2026-05-17) to fix the
    dual-writer SQLite corruption. ChromaDB now runs only as a
    library inside the gateway. The CHROMA_IMAGE placeholder
    assertion was kept for ~6 months pre-§55 and is no longer
    expected to be present — see
    ``test_docker_compose_has_no_chromadb_service`` for the
    pinning that enforces the removal.

    The 2026-06-07 supply-chain pass appended an ``@sha256:`` digest to
    each default tag (e.g.
    ``${POSTGRES_IMAGE:-pgvector/pgvector:pg16@sha256:…}``). The
    override form is what matters for the drill, so we assert the
    ``${VAR:-tag`` prefix (digest-tolerant) rather than the exact
    default — the drill sets POSTGRES_IMAGE / NEO4J_IMAGE to the target
    tag, which overrides the pinned default regardless of any digest."""
    p = REPO_ROOT / "docker-compose.yml"
    assert p.is_file()
    src = p.read_text()
    # Prefix match (no trailing `}`) so a digest-pinned default
    # `${POSTGRES_IMAGE:-pgvector/pgvector:pg16@sha256:…}` still passes.
    for placeholder in (
        "${POSTGRES_IMAGE:-pgvector/pgvector:pg16",
        "${NEO4J_IMAGE:-neo4j:5-community",
    ):
        assert placeholder in src, (
            f"docker-compose.yml must contain a `{placeholder}…}}` "
            "override so the version-upgrade drill's image override env "
            "vars actually flow through compose. The :-default form "
            "preserves live behaviour when no env var is set."
        )


def _assert_no_chromadb_leg(script: Path) -> None:
    """Pins PROGRAM §55: ChromaDB is no longer a compose service, so the
    drills must NOT try to bring up, query, or pre-flight a `chromadb`
    container.

    Once the chromadb service was removed from docker-compose.yml the
    old leg was broken four independent ways: an orphan
    ``up -d ... chromadb`` / ``ps -q chromadb`` that always came back
    empty; an overlay chromadb service with no image that made
    ``docker compose config`` invalid; a stale ``CHROMA_TARGET_TAG``
    default (chromadb/chroma:1.0) older than the gateway's pinned lib;
    and a ``CHR_ARCHIVE`` timestamp keyed off the postgres backup that
    no longer aligns with the gateway-owned chromadb tarballs. ChromaDB
    resilience is drilled by source_ledger_replay + embedding_rotation
    instead. (The header comment legitimately *mentions* chromadb to
    explain the exclusion, so we pin the operational tokens, not the
    word.)"""
    src = script.read_text()
    forbidden = {
        "up -d postgres neo4j chromadb": "must not `up` a chromadb service",
        "ps -q chromadb": "must not query a chromadb container",
        "CHR_CT": "must not reference a chromadb container handle",
        "CHROMA_TARGET_TAG": "must not pin a chromadb image tag",
        "crewai-team-chromadb-1": "must not pre-flight a chromadb live container",
    }
    for needle, why in forbidden.items():
        assert needle not in src, (
            f"{script.name} {why} (PROGRAM §55 removed the chromadb "
            f"compose service): found `{needle}`."
        )


def test_restore_drill_has_no_chromadb_leg() -> None:
    _assert_no_chromadb_leg(_drill_script("restore-drill.sh"))


def test_version_upgrade_drill_has_no_chromadb_leg() -> None:
    _assert_no_chromadb_leg(_drill_script("version-upgrade-drill.sh"))
