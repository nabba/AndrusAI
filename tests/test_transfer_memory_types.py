"""Tests for app.transfer_memory.types and the shared SkillDraft helper."""

from app.transfer_memory.types import (
    NegativeTransferTag,
    TransferEvent,
    TransferKind,
    TransferScope,
    domain_for_kind,
)


# ── TransferEvent roundtrip ─────────────────────────────────────────

def test_transfer_event_roundtrip():
    evt = TransferEvent(
        event_id="evt_1",
        kind=TransferKind.HEALING,
        source_id="src",
        summary="summary",
        project_origin="plg",
        payload={"k": "v"},
    )
    d = evt.to_dict()
    assert d["kind"] == "healing"

    evt2 = TransferEvent.from_dict(d)
    assert evt2.kind == TransferKind.HEALING
    assert evt2.source_id == "src"


def test_transfer_event_extra_keys_ignored():
    """Forward-compat: a JSON line with extra keys still parses."""
    evt = TransferEvent.from_dict({
        "event_id": "x",
        "kind": "healing",
        "source_id": "y",
        "future_field": "ignored",
    })
    assert evt.kind == TransferKind.HEALING


# ── domain_for_kind ─────────────────────────────────────────────────

def test_domain_for_kind_returns_default_for_all_kinds():
    """All known kinds get a non-empty domain."""
    for k in TransferKind:
        assert domain_for_kind(k) != ""


def test_transfer_scopes_canonical_values():
    assert TransferScope.SHADOW.value == "shadow"
    assert TransferScope.PROJECT_LOCAL.value == "project_local"
    assert TransferScope.SAME_DOMAIN_ONLY.value == "same_domain_only"
    assert TransferScope.GLOBAL_META.value == "global_meta"


def test_negative_transfer_tags_complete():
    expected = {
        "domain_mismatched_anchor",
        "false_validation_confidence",
        "misapplied_best_practice",
        "project_scope_leakage",
        "safety_boundary_conflict",
        "over_abstraction",
    }
    assert {t.value for t in NegativeTransferTag} == expected


# ── construct_skill_draft helper ────────────────────────────────────

def test_construct_skill_draft_traj_id_prefix():
    from app.self_improvement.types import construct_skill_draft

    draft = construct_skill_draft(
        topic="t",
        rationale="r",
        content_markdown="c" * 100,
        id_prefix="traj",
        novelty_at_creation=0.5,
    )
    assert draft.id.startswith("draft_traj_")
    assert draft.novelty_at_creation == 0.5


def test_construct_skill_draft_xfer_with_transfer_fields():
    from app.self_improvement.types import construct_skill_draft

    draft = construct_skill_draft(
        topic="t",
        rationale="r",
        content_markdown="c" * 100,
        id_prefix="xfer",
        source_kind="healing",
        source_domain="healing",
        transfer_scope="shadow",
        abstraction_score=0.7,
        leakage_risk=0.0,
        novelty_at_creation=1.0,
    )
    assert draft.id.startswith("draft_xfer_")
    assert draft.source_kind == "healing"
    assert draft.transfer_scope == "shadow"
    assert draft.abstraction_score == 0.7


def test_construct_skill_draft_default_proposed_kb_preserved():
    """When proposed_kb is empty, the SkillDraft default ('episteme')
    must still apply — caller can opt in to KB pre-classification."""
    from app.self_improvement.types import construct_skill_draft

    draft = construct_skill_draft(
        topic="t",
        rationale="r",
        content_markdown="c" * 100,
        novelty_at_creation=1.0,
    )
    assert draft.proposed_kb == "episteme"


def test_construct_skill_draft_supersedes_defaults_to_empty_list():
    from app.self_improvement.types import construct_skill_draft

    draft = construct_skill_draft(
        topic="t",
        rationale="r",
        content_markdown="c" * 100,
        novelty_at_creation=1.0,
    )
    assert draft.supersedes == []


def test_construct_skill_draft_no_id_prefix_yields_plain_draft_prefix():
    from app.self_improvement.types import construct_skill_draft

    draft = construct_skill_draft(
        topic="t",
        rationale="r",
        content_markdown="c" * 100,
        novelty_at_creation=1.0,
    )
    assert draft.id.startswith("draft_")
    assert not draft.id.startswith("draft_traj_")
    assert not draft.id.startswith("draft_xfer_")
