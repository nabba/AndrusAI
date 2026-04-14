"""Tests for workspace snapshot archive in workspace_versioning.py.

Covers Phase 8: evolution commit tagging and historical state exploration.
Uses a real git repo in tmp_path for integration-level testing.
"""
import os
import sys
import subprocess
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.test_metrics import _FakeSettings
import app.config as config_mod
config_mod.get_settings = lambda: _FakeSettings()
config_mod.get_anthropic_api_key = lambda: "fake-key"
config_mod.get_gateway_secret = lambda: "a" * 64


def _init_git_workspace(tmp_path):
    """Initialize a git repo in tmp_path with initial commit."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, env=env)
    (tmp_path / "test.txt").write_text("initial")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), capture_output=True, env=env)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True, env=env)
    return env


class TestIsEvolutionCommit:
    def test_evolution_prefix(self):
        from app.workspace_versioning import _is_evolution_commit
        assert _is_evolution_commit("evolution: improved coder prompt") is True

    def test_keep_keyword(self):
        from app.workspace_versioning import _is_evolution_commit
        assert _is_evolution_commit("exp_001: keep — improved web search") is True

    def test_promote_keyword(self):
        from app.workspace_versioning import _is_evolution_commit
        assert _is_evolution_commit("promote variant to production") is True

    def test_evo_prefix(self):
        from app.workspace_versioning import _is_evolution_commit
        assert _is_evolution_commit("evo: new strategy") is True

    def test_regular_commit(self):
        from app.workspace_versioning import _is_evolution_commit
        assert _is_evolution_commit("fix typo in README") is False

    def test_case_insensitive(self):
        from app.workspace_versioning import _is_evolution_commit
        assert _is_evolution_commit("EVOLUTION: big change") is True


class TestEvolutionTagging:
    """Test that workspace_commit() creates evo- tags for evolution commits."""

    def test_tags_evolution_commit(self, tmp_path, monkeypatch):
        import app.workspace_versioning as wv
        monkeypatch.setattr(wv, "WORKSPACE", tmp_path)
        monkeypatch.setattr(wv, "LOCK_FILE", tmp_path / ".workspace.lock")
        _init_git_workspace(tmp_path)

        (tmp_path / "skill.md").write_text("# New Skill")
        sha = wv.workspace_commit("evolution: added new skill")
        assert sha  # non-empty means commit succeeded

        # Check tag was created
        result = subprocess.run(
            ["git", "tag", "-l", "evo-*"],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert result.stdout.strip(), "No evo- tag found"
        assert result.stdout.strip().startswith("evo-")

    def test_no_tag_for_regular_commit(self, tmp_path, monkeypatch):
        import app.workspace_versioning as wv
        monkeypatch.setattr(wv, "WORKSPACE", tmp_path)
        monkeypatch.setattr(wv, "LOCK_FILE", tmp_path / ".workspace.lock")
        _init_git_workspace(tmp_path)

        (tmp_path / "notes.txt").write_text("some notes")
        wv.workspace_commit("updated notes")

        result = subprocess.run(
            ["git", "tag", "-l", "evo-*"],
            cwd=str(tmp_path), capture_output=True, text=True,
        )
        assert not result.stdout.strip(), "Regular commit should not get evo- tag"


class TestListEvolutionTags:
    def test_lists_tags(self, tmp_path, monkeypatch):
        import app.workspace_versioning as wv
        monkeypatch.setattr(wv, "WORKSPACE", tmp_path)
        monkeypatch.setattr(wv, "LOCK_FILE", tmp_path / ".workspace.lock")
        env = _init_git_workspace(tmp_path)

        # Create tagged evolution commits
        for i in range(3):
            (tmp_path / f"skill_{i}.md").write_text(f"# Skill {i}")
            wv.workspace_commit(f"evolution: skill {i}")

        tags = wv.list_evolution_tags(10)
        assert len(tags) == 3
        for t in tags:
            assert t["tag"].startswith("evo-")
            assert t["sha"]

    def test_returns_empty_when_no_tags(self, tmp_path, monkeypatch):
        import app.workspace_versioning as wv
        monkeypatch.setattr(wv, "WORKSPACE", tmp_path)
        monkeypatch.setattr(wv, "LOCK_FILE", tmp_path / ".workspace.lock")
        _init_git_workspace(tmp_path)

        tags = wv.list_evolution_tags()
        assert tags == []

    def test_respects_limit(self, tmp_path, monkeypatch):
        import app.workspace_versioning as wv
        monkeypatch.setattr(wv, "WORKSPACE", tmp_path)
        monkeypatch.setattr(wv, "LOCK_FILE", tmp_path / ".workspace.lock")
        _init_git_workspace(tmp_path)

        for i in range(5):
            (tmp_path / f"s{i}.md").write_text(f"# S{i}")
            wv.workspace_commit(f"evolution: s{i}")

        tags = wv.list_evolution_tags(n=2)
        assert len(tags) == 2


class TestReadFileAtTag:
    def test_reads_file_content(self, tmp_path, monkeypatch):
        import app.workspace_versioning as wv
        monkeypatch.setattr(wv, "WORKSPACE", tmp_path)
        monkeypatch.setattr(wv, "LOCK_FILE", tmp_path / ".workspace.lock")
        _init_git_workspace(tmp_path)

        # Create initial version
        (tmp_path / "config.json").write_text('{"v": 1}')
        wv.workspace_commit("evolution: v1")

        tags = wv.list_evolution_tags(1)
        assert len(tags) == 1

        # Modify file
        (tmp_path / "config.json").write_text('{"v": 2}')
        wv.workspace_commit("evolution: v2")

        # Read old version via tag
        content = wv.read_file_at_tag(tags[0]["tag"], "config.json")
        assert content is not None
        assert '"v": 1' in content

    def test_rejects_non_evo_tag(self):
        from app.workspace_versioning import read_file_at_tag
        result = read_file_at_tag("malicious-tag", "config.json")
        assert result is None

    def test_rejects_path_traversal_in_tag(self):
        from app.workspace_versioning import read_file_at_tag
        result = read_file_at_tag("evo-../../../etc/passwd", "file.txt")
        assert result is None

    def test_returns_none_for_missing_file(self, tmp_path, monkeypatch):
        import app.workspace_versioning as wv
        monkeypatch.setattr(wv, "WORKSPACE", tmp_path)
        monkeypatch.setattr(wv, "LOCK_FILE", tmp_path / ".workspace.lock")
        _init_git_workspace(tmp_path)

        (tmp_path / "exists.txt").write_text("here")
        wv.workspace_commit("evolution: add file")

        tags = wv.list_evolution_tags(1)
        content = wv.read_file_at_tag(tags[0]["tag"], "nonexistent.txt")
        assert content is None


class TestWorkspaceDiffFromTag:
    def test_returns_diff_stat(self, tmp_path, monkeypatch):
        import app.workspace_versioning as wv
        monkeypatch.setattr(wv, "WORKSPACE", tmp_path)
        monkeypatch.setattr(wv, "LOCK_FILE", tmp_path / ".workspace.lock")
        _init_git_workspace(tmp_path)

        (tmp_path / "a.txt").write_text("v1")
        wv.workspace_commit("evolution: v1")
        tags = wv.list_evolution_tags(1)

        (tmp_path / "b.txt").write_text("new file")
        wv.workspace_commit("evolution: add b")

        diff = wv.workspace_diff_from_tag(tags[0]["tag"])
        assert "b.txt" in diff

    def test_rejects_non_evo_tag(self):
        from app.workspace_versioning import workspace_diff_from_tag
        assert workspace_diff_from_tag("bad-tag") == ""
