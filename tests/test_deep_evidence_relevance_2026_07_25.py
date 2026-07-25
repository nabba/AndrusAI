"""Deep-evidence retrieval must reject hits that aren't about the question.

`_usable_deep_evidence` only ever checked STRUCTURE — source class, identifier,
excerpt length, fetched-ness, KB score. Nothing checked topicality.

Live on 2026-07-25, for "compare the economic and environmental trade-offs of
Estonia's oil shale industry versus renewable energy", retrieval returned ten
structurally-perfect sources with **zero** on topic. The draft's own account:

    All retrieved sources are off-topic: AI research-synthesis notes on a
    safety-invariant architecture [S1][S2][S3][S9][S10]; a generic
    probabilistic energy-forecasting model with no Estonia-specific content
    [S4]; a 250B-parameter language model report [S5]; a solar-flare EUV
    irradiance predictor (solar physics, not solar power) [S6]; and two
    text-diff web tools [S7][S8].

Every gate downstream behaved correctly on evidence that should never have
reached it. The negative fixtures below are those exact items.

The over-rejection risk is the one that matters: too strict a filter converts
working answers into "retrieved no evidence sources" blocks, which is worse than
a slightly noisy evidence set. Roughly half of these tests therefore pin what
must still be ACCEPTED.
"""
import pytest

_QUESTION = (
    "compare the economic and environmental trade-offs of Estonia's oil shale "
    "industry versus renewable energy, and evaluate which path serves the "
    "country better long-term"
)


def _hit(title, text, source="web", ident="https://example.com/a"):
    """A structurally-valid LiteratureHit."""
    from app.research.literature import LiteratureHit

    return LiteratureHit(
        source=source, id=ident, title=title,
        text=text, metadata={"url": ident, "content_fetched": True},
    )


# ── must REJECT: the exact off-topic items observed live ─────────────────

@pytest.mark.parametrize("title,text", [
    (
        "A safety-invariant architecture for research synthesis agents",
        "We describe an agent architecture preserving safety invariants across "
        "delegated research synthesis tasks, with governance gates and audit.",
    ),
    (
        "Probabilistic energy forecasting with deep ensembles",
        "A generic probabilistic model for short-term energy demand forecasting "
        "evaluated on public load datasets across several markets.",
    ),
    (
        "A 250B-parameter language model: training report",
        "We report the training of a 250 billion parameter transformer language "
        "model, covering data mixture, scaling and evaluation results.",
    ),
    (
        "Predicting solar flare EUV irradiance from magnetograms",
        "A solar physics model predicting extreme-ultraviolet irradiance during "
        "flare events from photospheric magnetogram sequences.",
    ),
    (
        "difftool — compare two texts online",
        "Paste two documents to compare them line by line and export the diff "
        "as HTML. Free online text comparison utility.",
    ),
])
def test_rejects_the_off_topic_hits_seen_live(title, text):
    dp = pytest.importorskip("app.research.deep_path")

    relevant, why = dp._topically_relevant(_QUESTION, _hit(title, text))

    assert not relevant, f"should be rejected as off topic, got: {why}"


def test_rejects_our_own_kb_notes_for_an_external_question():
    """The self-referential case, which needs no separate filter."""
    dp = pytest.importorskip("app.research.deep_path")

    own_notes = _hit(
        "Governance ratchet and Tier-3 protocol notes",
        "Internal notes on the change-request pipeline, TIER_IMMUTABLE scope "
        "and the operator approval gate for self-modification.",
        source="kb", ident="kb:chunk:internal-notes",
    )
    relevant, _ = dp._topically_relevant(_QUESTION, own_notes)
    assert not relevant


# ── must ACCEPT: genuinely on-topic hits ─────────────────────────────────

def test_accepts_a_directly_on_topic_source():
    dp = pytest.importorskip("app.research.deep_path")

    hit = _hit(
        "Estonia's oil shale industry: economic and environmental review",
        "Oil shale accounts for a large share of Estonia's energy production; "
        "this review compares its economics against renewable alternatives and "
        "the environmental cost of continued extraction.",
    )
    relevant, why = dp._topically_relevant(_QUESTION, hit)
    assert relevant, why


def test_accepts_a_source_using_estonia_when_the_question_says_estonian():
    """Morphology must not sink a relevant source."""
    dp = pytest.importorskip("app.research.deep_path")

    question = (
        "write me a critical report on the Estonian dairy industry's business "
        "practices over the last decade, with sources"
    )
    hit = _hit(
        "Dairy farming in Estonia: structure and consolidation 2015-2025",
        "The dairy industry in Estonia consolidated sharply over the decade, "
        "with herd sizes rising and the number of producers falling; business "
        "practices and milk pricing are examined.",
    )
    relevant, why = dp._topically_relevant(question, hit)
    assert relevant, why


@pytest.mark.parametrize("question,title,text", [
    # gs_report_no_evaluate — DELIVERED in the 07-25 baseline. The first cut of
    # this filter used exact-token matching and rejected it outright ("shares 0
    # of 6"): Estonian/Estonia and forests/forest share no exact token. That
    # would have converted a working answer into a no-evidence block.
    (
        "make me a report on how Estonian forests have changed over the years",
        "Forest area and growing stock in Estonia, 2000-2024",
        "The forest area of Estonia has grown; growing stock rose steadily over "
        "two decades according to the national forest inventory.",
    ),
    # gs_research_light — the cheapest delivered question; must stay delivered.
    (
        "what is Estonia's current population?",
        "Population figures | Statistics Estonia",
        "The population of Estonia was 1,360,745 on 1 January 2026.",
    ),
    # gs_report_forest — the original incident question, fixed in 06caad82.
    (
        "please make me a report on estona forest health and deforestation data "
        "over the years",
        "Forest health monitoring in Estonia",
        "Deforestation and forest health indicators for Estonia over two decades.",
    ),
])
def test_golden_set_questions_that_deliver_are_not_broken(question, title, text):
    """Guards the filter's real risk: silently breaking what already works."""
    dp = pytest.importorskip("app.research.deep_path")

    relevant, why = dp._topically_relevant(question, _hit(title, text))

    assert relevant, (
        f"a question that delivers in the baseline would now find no evidence: "
        f"{why}"
    )


def test_term_matches_absorbs_ordinary_morphology():
    dp = pytest.importorskip("app.research.deep_path")

    assert dp._term_matches("forests", {"forest", "area"})
    assert dp._term_matches("estonian", {"estonia"})
    assert dp._term_matches("renewable", {"renewables"})
    assert not dp._term_matches("estonia", {"finland", "sweden"})


def test_accepts_partial_topical_overlap():
    """Permissive by design — 2 shared terms plus the entity is enough."""
    dp = pytest.importorskip("app.research.deep_path")

    hit = _hit(
        "Renewable energy targets in the Baltic states",
        "Estonia, Latvia and Lithuania have diverging renewable energy "
        "trajectories; this brief reviews policy instruments and grid effects.",
    )
    relevant, why = dp._topically_relevant(_QUESTION, hit)
    assert relevant, why


def test_accepts_self_referential_notes_when_the_question_is_about_the_system():
    """Legitimate self-inquiry must not be fenced off by the KB exclusion."""
    dp = pytest.importorskip("app.research.deep_path")

    question = "how does my safety-invariant research synthesis architecture work?"
    own_notes = _hit(
        "A safety-invariant architecture for research synthesis agents",
        "We describe an agent architecture preserving safety invariants across "
        "delegated research synthesis tasks, with governance gates and audit.",
        source="kb", ident="kb:chunk:internal-notes",
    )
    relevant, why = dp._topically_relevant(question, own_notes)
    assert relevant, why


def test_question_with_no_distinctive_terms_is_not_filtered():
    """Fail open rather than reject everything."""
    dp = pytest.importorskip("app.research.deep_path")

    relevant, _ = dp._topically_relevant("what is it?", _hit("Anything", "Text"))
    assert relevant


def test_question_without_entities_falls_back_to_overlap_only():
    dp = pytest.importorskip("app.research.deep_path")

    question = "compare wind turbine maintenance costs against solar panel upkeep"
    hit = _hit(
        "Maintenance costs of wind turbines versus solar panels",
        "A lifecycle comparison of wind turbine maintenance against solar panel "
        "upkeep across two decades of operation.",
    )
    relevant, why = dp._topically_relevant(question, hit)
    assert relevant, why


# ── helpers ──────────────────────────────────────────────────────────────

def test_entity_terms_skips_the_leading_word_and_strips_possessives():
    dp = pytest.importorskip("app.research.deep_path")

    assert dp._entity_terms(_QUESTION) == {"estonia"}
    # "Compare" leads the sentence — capitalised by grammar, not an entity.
    assert "compare" not in dp._entity_terms("Compare Estonia and Latvia")
    assert dp._entity_terms("Compare Estonia and Latvia") == {"estonia", "latvia"}


def test_entity_present_matches_across_morphology():
    dp = pytest.importorskip("app.research.deep_path")

    assert dp._entity_present({"estonian"}, {"estonia", "dairy"})
    assert dp._entity_present({"estonia"}, {"estonian", "forest"})
    assert not dp._entity_present({"estonia"}, {"finland", "sweden"})
    assert dp._entity_present(set(), {"anything"}), "no entities = gate skipped"


# ── the retrieval loop actually applies the filter ───────────────────────

def test_collect_deep_evidence_drops_off_topic_hits(monkeypatch):
    dp = pytest.importorskip("app.research.deep_path")

    on_topic = _hit(
        "Estonia's oil shale industry: economic and environmental review",
        "Oil shale accounts for a large share of Estonia's energy production, "
        "and this review compares its economics against renewable alternatives "
        "with attention to environmental cost.",
        ident="https://example.com/on-topic",
    )
    off_topic = _hit(
        "Predicting solar flare EUV irradiance from magnetograms",
        "A solar physics model predicting extreme-ultraviolet irradiance during "
        "flare events from photospheric magnetogram sequences over many cycles.",
        ident="https://example.com/off-topic",
    )

    merged = dp.collect_deep_evidence(
        _QUESTION,
        planner_fn=lambda q: [],
        search_fn=lambda q: [on_topic, off_topic],
    )

    ids = [h.id for h in merged]
    assert "https://example.com/on-topic" in ids
    assert "https://example.com/off-topic" not in ids


def test_collect_deep_evidence_judges_against_the_original_question(monkeypatch):
    """A drifted subquery must not smuggle in a hit that's off topic overall."""
    dp = pytest.importorskip("app.research.deep_path")

    drifted = _hit(
        "Predicting solar flare EUV irradiance from magnetograms",
        "A solar physics model predicting extreme-ultraviolet irradiance during "
        "flare events from photospheric magnetogram sequences over many cycles.",
        ident="https://example.com/drifted",
    )

    merged = dp.collect_deep_evidence(
        _QUESTION,
        planner_fn=lambda q: ["solar irradiance prediction"],
        search_fn=lambda q: [drifted],
    )

    assert merged == [], "hit matching only the drifted subquery must be dropped"
