"""The benchmark-score lookup must not issue one Postgres query per model.

``create_specialist_llm`` → ``select_model`` → ``resolve_role_default`` →
``get_combined_scores`` → ``get_scores`` → ``get_external_score`` → Postgres.

``get_scores`` looped over the WHOLE runtime catalog calling
``get_external_score`` once per model, then called it again per candidate. With
360 catalog entries live that is ~720 synchronous queries **per agent
construction**, against the fixed 24-connection ``CONTROL_PLANE_POOL_MAX``
pool.

That is the mechanism behind the 2026-07-24 incident: raising
``thread_pool_size`` 6→16 multiplied that fan-out across more concurrent
constructions and the gateway wedged for 36.5s with 7 threads blocked in
``psycopg2/pool.py`` inside this exact chain
(``workspace/healing/loop_stalls/20260724T171644Z.txt``), force-restarted 3× in
2.5h. The concurrency bump was reverted; this is the prerequisite fix.

These tests pin the query count, not just the result — a correctness-only test
would have passed on the broken version.
"""
import pytest


@pytest.fixture()
def ranks(monkeypatch):
    """llm_external_ranks with a counting fake DB and a clean cache."""
    mod = pytest.importorskip("app.llm_external_ranks")
    mod.invalidate_external_score_cache()

    calls = []

    def fake_execute(sql, params=(), fetch=False):
        calls.append((sql, params))
        return [
            {"model_id": "model-a", "metric": "quality", "value": 0.9},
            {"model_id": "model-a", "metric": "elo", "value": 0.7},
            {"model_id": "model-b", "metric": "quality", "value": 0.5},
            {"model_id": "model-c", "metric": "quality", "value": None},
        ]

    import app.control_plane.db as db
    monkeypatch.setattr(db, "execute", fake_execute)
    try:
        yield mod, calls
    finally:
        mod.invalidate_external_score_cache()


def test_bulk_fetch_uses_exactly_one_query(ranks):
    mod, calls = ranks

    scores = mod.get_external_scores_bulk("research")

    assert len(calls) == 1, f"expected 1 query, got {len(calls)}"
    assert scores["model-a"] == pytest.approx(0.8)   # mean(0.9, 0.7)
    assert scores["model-b"] == pytest.approx(0.5)
    assert "model-c" not in scores, "a NULL value must not become a score"


def test_repeated_lookups_across_many_models_stay_at_one_query(ranks):
    """The old code issued one query per model_id. This is the regression."""
    mod, calls = ranks

    for name in [f"model-{i}" for i in range(200)]:
        mod.get_external_score(name, "research")

    assert len(calls) == 1, (
        f"200 model lookups issued {len(calls)} queries — the per-model "
        "fan-out is back"
    )


def test_get_external_score_agrees_with_the_bulk_map(ranks):
    mod, _ = ranks

    bulk = mod.get_external_scores_bulk("research")
    assert mod.get_external_score("model-a", "research") == bulk["model-a"]
    assert mod.get_external_score("model-b", "research") == bulk["model-b"]
    assert mod.get_external_score("model-c", "research") is None
    assert mod.get_external_score("never-seen", "research") is None


def test_cache_expires(ranks, monkeypatch):
    mod, calls = ranks

    clock = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    mod.get_external_scores_bulk("research")
    assert len(calls) == 1

    clock["t"] += mod._BULK_CACHE_TTL_S - 1
    mod.get_external_scores_bulk("research")
    assert len(calls) == 1, "still inside the TTL"

    clock["t"] += 2
    mod.get_external_scores_bulk("research")
    assert len(calls) == 2, "must refetch once the TTL lapses"


def test_cache_is_keyed_per_task_type(ranks):
    mod, calls = ranks

    mod.get_external_scores_bulk("research")
    mod.get_external_scores_bulk("coding")
    mod.get_external_scores_bulk("research")

    assert len(calls) == 2, "distinct task types must not share a cache entry"


def test_invalidation_forces_a_refetch(ranks):
    mod, calls = ranks

    mod.get_external_scores_bulk("research")
    mod.invalidate_external_score_cache()
    mod.get_external_scores_bulk("research")

    assert len(calls) == 2


def test_bulk_fetch_fails_open_on_a_db_error(monkeypatch):
    """Selection must degrade to internal telemetry, never raise."""
    mod = pytest.importorskip("app.llm_external_ranks")
    mod.invalidate_external_score_cache()

    def boom(*a, **k):
        raise RuntimeError("pool exhausted")

    import app.control_plane.db as db
    monkeypatch.setattr(db, "execute", boom)
    try:
        assert mod.get_external_scores_bulk("research") == {}
        assert mod.get_external_score("model-a", "research") is None
    finally:
        mod.invalidate_external_score_cache()


def test_get_scores_blend_does_not_fan_out_over_the_catalog(monkeypatch):
    """The end-to-end pin: one query no matter how big the catalog is."""
    bench = pytest.importorskip("app.llm_benchmarks")
    ranks_mod = pytest.importorskip("app.llm_catalog")
    external = pytest.importorskip("app.llm_external_ranks")
    external.invalidate_external_score_cache()

    calls = []

    def fake_execute(sql, params=(), fetch=False):
        calls.append(sql)
        return [{"model_id": "model-a", "metric": "quality", "value": 0.9}]

    import app.control_plane.db as db
    monkeypatch.setattr(db, "execute", fake_execute)

    # A realistically-sized catalog — live is ~360 entries.
    big_catalog = {f"model-{i}": {"tier": "mid"} for i in range(360)}
    big_catalog["model-a"] = {"tier": "mid"}
    monkeypatch.setattr(ranks_mod, "CATALOG", big_catalog)
    monkeypatch.setattr(bench, "_internal_scores", lambda tt: {"model-a": 0.6})

    try:
        scores = bench.get_scores("research", blend_external=True)
    finally:
        external.invalidate_external_score_cache()

    assert len(calls) == 1, (
        f"a 360-entry catalog produced {len(calls)} queries — the old code "
        "issued ~720 here, which is what exhausted the 24-connection pool"
    )
    # Blending still works: 0.7*internal + 0.3*external by default.
    assert "model-a" in scores
    assert 0.6 <= scores["model-a"] <= 0.9
