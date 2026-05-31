"""Cgroup-memory-pressure idle gating (PROGRAM §82).

The gateway runs under an 8 GB Docker memory limit; an OOM SIGKILL → cold
restart is the secondary cause behind the §81 watchdog restart floods. These
pin (a) the cgroup reader's graceful behaviour off-Linux / with no limit, and
(b) the policy predicate that defers heavy idle work near the cgroup ceiling.

Pure-logic tests: ``should_defer_heavy_work`` is fed a constructed snapshot,
so no gateway env / pydantic is required.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.substrate.policy import ResourcePolicy, should_defer_heavy_work  # noqa: E402
from app.substrate import status as status_mod  # noqa: E402


def _snap(resources: dict, inflight: int = 0) -> SimpleNamespace:
    return SimpleNamespace(resources=resources, inflight_tasks=inflight)


# ── Policy predicate ───────────────────────────────────────────────────
def test_defers_when_near_cgroup_limit():
    reason = should_defer_heavy_work(_snap({"cgroup_mem_used_fraction": 0.93}))
    assert reason is not None
    assert "cgroup_mem" in reason


def test_runs_when_below_cgroup_threshold():
    assert should_defer_heavy_work(_snap({"cgroup_mem_used_fraction": 0.50})) is None


def test_skips_when_fraction_absent():
    # None ⇒ predicate skipped (fail-open) — e.g. non-Linux or no limit set.
    assert should_defer_heavy_work(_snap({"cgroup_mem_used_fraction": None})) is None
    assert should_defer_heavy_work(_snap({})) is None


def test_threshold_is_tunable():
    pol = ResourcePolicy(max_cgroup_mem_fraction=0.5)
    assert should_defer_heavy_work(_snap({"cgroup_mem_used_fraction": 0.6}), pol) is not None
    assert should_defer_heavy_work(_snap({"cgroup_mem_used_fraction": 0.4}), pol) is None


def test_exactly_at_threshold_defers():
    # >= boundary: at the threshold we defer (conservative).
    assert should_defer_heavy_work(_snap({"cgroup_mem_used_fraction": 0.90})) is not None


# ── Cgroup reader ──────────────────────────────────────────────────────
def test_reader_never_raises_and_handles_absent_files():
    # On the (non-cgroup) test host this returns (None, None); it must never
    # raise regardless of platform.
    used, limit = status_mod._read_cgroup_memory()
    assert used is None or isinstance(used, int)
    assert limit is None or isinstance(limit, int)


def test_v2_no_limit_returns_used_with_none_limit(tmp_path, monkeypatch):
    cur = tmp_path / "memory.current"
    mx = tmp_path / "memory.max"
    cur.write_text("1048576")
    mx.write_text("max")

    def fake_read(self, *a, **k):
        name = self.name
        if name == "memory.current":
            return cur.read_text()
        if name == "memory.max":
            return mx.read_text()
        raise OSError("nope")

    monkeypatch.setattr("pathlib.Path.read_text", fake_read)
    used, limit = status_mod._read_cgroup_memory()
    assert used == 1048576
    assert limit is None  # "max" sentinel → no limit


def test_probe_populates_fraction(monkeypatch):
    from app.substrate.status import SubstrateStatus, _probe_resources
    monkeypatch.setattr(status_mod, "_read_cgroup_memory",
                        lambda: (4 * 1024 * 1024 * 1024, 8 * 1024 * 1024 * 1024))
    snap = SubstrateStatus()
    _probe_resources(snap)
    assert snap.resources["cgroup_mem_used_fraction"] == 0.5
    assert snap.resources["cgroup_mem_limit_mb"] == 8192.0
