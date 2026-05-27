"""Tests for app.library_radar.trial_runner.

Regression coverage for the 2026-05-27 incident: the runner called a
non-existent ``bridge_store.list_all()`` whose AttributeError was
swallowed, so every pending trial deferred as "no proposal" and the
trial→adoption pipeline silently did nothing for ~11 days. Plus the
PyPI candidate gate added in the same fix.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROPOSAL_BRIDGE_DIR", str(tmp_path / "proposal_bridge"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    yield


def _seed_proposal(signature: str, slug: str) -> None:
    from app.proposal_bridge import stage

    stage(
        source="library_radar",
        signature=signature,
        title="LangGraph",
        body_markdown="# proposal\n\nbody.\n",
        target_path=f"docs/proposed_libraries/{signature}-{slug}.md",
        coding_session_spec={
            "files": [
                {
                    "action": "create",
                    "path": f"tests/library_trials/test_{slug}_smoke.py",
                }
            ]
        },
    )


# ── regression pin: the API-name bug that caused the silent stall ────────


def test_trial_runner_uses_list_proposals_not_list_all() -> None:
    from app.library_radar import trial_runner

    src = inspect.getsource(trial_runner)
    assert "list_proposals(source=" in src
    # The exact wrong call that silently disabled the pipeline.
    assert ".list_all(" not in src


def test_run_one_pass_resolves_proposal_and_does_not_defer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.library_radar import trial_runner, trial_state

    sig = "aabbccddeeff"
    _seed_proposal(sig, "langgraph")
    trial_state.mark_pending(signature=sig, slug="langgraph", candidates=["langgraph"])

    seen: list[tuple[str, str]] = []

    def _stub_trial(state, proposal):
        seen.append((state.signature, proposal.signature))
        trial_state.mark_failed(state.signature, error="stub")
        return "failed", "stub"

    monkeypatch.setattr(trial_runner, "_run_one_trial", _stub_trial)

    out = trial_runner.run_one_pass()

    # The proposal was resolved from the bridge store and handed to the
    # trial. Before the fix this list would be empty and deferred==1.
    assert seen == [(sig, sig)]
    assert out["considered"] == 1
    assert out["failed"] == 1
    assert out["deferred"] == 0


def test_run_one_pass_no_pending_is_noop() -> None:
    from app.library_radar import trial_runner

    out = trial_runner.run_one_pass()
    assert out["status"] == "no_pending"


# ── candidate gate ───────────────────────────────────────────────────────


def _state(package_name: str, candidates: list[str]):
    from app.library_radar.trial_state import TrialState

    return TrialState(
        signature="s", slug="s", package_name=package_name, candidates=candidates
    )


def test_select_candidate_picks_first_pypi_resolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.library_radar import trial_runner

    # Only the brand token resolves; the tokeniser noise does not.
    resolved = {"openai": "exists"}
    monkeypatch.setattr(
        trial_runner, "_pypi_status", lambda n: resolved.get(n, "absent")
    )

    pkg, terminal, _ = trial_runner._select_candidate(
        _state("openai", ["openai", "responses", "official", "designed"])
    )
    assert pkg == "openai"
    assert terminal is False


def test_select_candidate_marks_terminal_when_none_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.library_radar import trial_runner

    monkeypatch.setattr(trial_runner, "_pypi_status", lambda n: "absent")

    pkg, terminal, reason = trial_runner._select_candidate(
        _state("openrouter", ["openrouter", "integrated", "fetch"])
    )
    assert pkg is None
    assert terminal is True  # → caller marks the discovery FAILED
    assert "no candidate resolved" in reason


def test_select_candidate_stays_pending_when_pypi_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.library_radar import trial_runner

    monkeypatch.setattr(trial_runner, "_pypi_status", lambda n: "unknown")

    pkg, terminal, _ = trial_runner._select_candidate(
        _state("openai", ["openai", "responses"])
    )
    assert pkg is None
    assert terminal is False  # → caller leaves PENDING, retries next pass


def test_pypi_status_maps_404_to_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    from app.library_radar import trial_runner

    def _raise_404(*_a, **_k):
        raise urllib.error.HTTPError(
            url="x", code=404, msg="nf", hdrs=None, fp=None
        )

    monkeypatch.setattr(trial_runner.urllib.request, "urlopen", _raise_404)
    assert trial_runner._pypi_status("definitely-not-a-real-pkg-xyz") == "absent"


def test_pypi_status_maps_network_error_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.library_radar import trial_runner

    def _raise(*_a, **_k):
        raise OSError("network down")

    monkeypatch.setattr(trial_runner.urllib.request, "urlopen", _raise)
    assert trial_runner._pypi_status("openai") == "unknown"
