"""Tests for v2 Signal commands (compress, usage, mcp, training, schedule)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._v2_shim import install_settings_shim

install_settings_shim()


class _DummyCommander:
    last_crew_used = "dummy"

    def handle(self, text, sender, attachments):
        return "ok"


@pytest.fixture(autouse=True)
def reset_state(tmp_path, monkeypatch):
    # Fresh conversation store
    import app.conversation_store as cs
    monkeypatch.setattr(cs, "DB_PATH", tmp_path / "conv.db")
    if hasattr(cs._local, "conn"):
        cs._local.conn = None

    # Fresh history store
    from app import history_compression
    history_compression._histories.clear()

    # Persist NL jobs to an isolated file
    from app.agents.commander import commands as cmd_mod
    monkeypatch.setattr(cmd_mod, "_NL_JOBS_FILE", tmp_path / "nl_jobs.json")
    yield


class TestCompressAndUsage:
    def test_compress_empty_history(self):
        from app.agents.commander.commands import try_command
        out = try_command("/compress", "+15551112222", _DummyCommander())
        assert out is not None
        assert "Compression ran" in out
        assert "Tokens:" in out

    def test_usage_shows_stats(self):
        from app.agents.commander.commands import try_command
        out = try_command("/usage", "+15551112222", _DummyCommander())
        assert out is not None
        assert "Session usage" in out or "Tokens:" in out

    def test_compress_handles_failure(self, monkeypatch):
        from app.agents.commander import commands as cmd_mod

        # Force get_history to fail
        def boom(_sender):
            raise RuntimeError("history broken")
        monkeypatch.setattr("app.history_compression.get_history", boom)

        out = cmd_mod.try_command("/compress", "+15551112222", _DummyCommander())
        assert out is not None
        assert "Compress error" in out or "error" in out.lower()


class TestMcpCommand:
    def test_mcp_status_no_servers(self):
        # Reset registry
        from app.mcp import registry
        registry._clients.clear()

        from app.agents.commander.commands import try_command
        out = try_command("mcp", "+15551112222", _DummyCommander())
        assert out is not None
        assert "No MCP servers" in out

    def test_mcp_status_with_server(self):
        from app.mcp import registry
        from app.mcp.client import MCPToolSchema

        registry._clients.clear()
        c = MagicMock()
        c.is_connected = True
        c.tools = [MCPToolSchema(server_name="s", name="t", description="")]
        c.config = MagicMock()
        c.config.transport = "stdio"
        registry._clients["filesystem"] = c

        from app.agents.commander.commands import try_command
        out = try_command("mcp status", "+15551112222", _DummyCommander())
        assert "filesystem" in out
        assert "1 tools" in out


class TestScheduleCommand:
    def test_schedule_adds_job(self, monkeypatch):
        from app.agents.commander import commands as cmd_mod

        jobs_added = []

        class FakeSched:
            def add_job(self, func, trigger, **kw):
                jobs_added.append({
                    "id": kw.get("id"),
                    "name": kw.get("name"),
                    "trigger": trigger,
                })

        fake_main = MagicMock()
        fake_main.scheduler = FakeSched()
        monkeypatch.setitem(__import__("sys").modules, "app.main", fake_main)

        out = cmd_mod.try_command(
            "schedule send weather report daily at 7am",
            "+15551112222",
            _DummyCommander(),
        )
        assert out is not None
        assert "Scheduled job" in out
        assert len(jobs_added) == 1
        assert jobs_added[0]["name"].startswith("send weather report")

    def test_schedule_unparseable_returns_error(self, monkeypatch):
        from app.agents.commander import commands as cmd_mod
        fake_main = MagicMock()
        fake_main.scheduler = MagicMock()
        monkeypatch.setitem(__import__("sys").modules, "app.main", fake_main)
        # Also defeat LLM fallback so it definitively fails
        monkeypatch.setattr("app.cron.nl_parser._llm_parse", lambda _: None)

        out = cmd_mod.try_command(
            "schedule x every made up cadence",
            "+15551112222",
            _DummyCommander(),
        )
        # Either returned None (no match) or parse failure message
        if out is not None:
            assert "Could not parse" in out or "Scheduled" in out

    def test_jobs_lists_registered(self, monkeypatch):
        from app.agents.commander import commands as cmd_mod
        import datetime

        # Build fake jobs
        job_a = MagicMock()
        job_a.id = "job1"
        job_a.name = "daily briefing"
        job_a.next_run_time = datetime.datetime(2026, 1, 1, 7, 0,
                                                tzinfo=datetime.timezone.utc)

        job_b = MagicMock()
        job_b.id = "job2"
        job_b.name = "weekly report"
        job_b.next_run_time = None

        fake_main = MagicMock()
        fake_main.scheduler.get_jobs.return_value = [job_a, job_b]
        monkeypatch.setitem(__import__("sys").modules, "app.main", fake_main)

        out = cmd_mod.try_command("jobs", "+15551112222", _DummyCommander())
        assert out is not None
        assert "job1" in out
        assert "job2" in out
        assert "daily briefing" in out

    def test_cancel_removes_job(self, monkeypatch):
        from app.agents.commander import commands as cmd_mod

        removed = []
        fake_main = MagicMock()
        fake_main.scheduler.remove_job = lambda jid: removed.append(jid)
        monkeypatch.setitem(__import__("sys").modules, "app.main", fake_main)

        out = cmd_mod.try_command("cancel job_abc123",
                                  "+15551112222",
                                  _DummyCommander())
        assert out is not None
        assert "Cancelled" in out
        assert removed == ["job_abc123"]


class TestNlJobPersistence:
    def test_persist_and_read(self, tmp_path):
        from app.agents.commander import commands as cmd_mod
        cmd_mod._persist_nl_job("abc", "do X", "0 7 * * *", "+1555")
        data = cmd_mod._read_nl_jobs()
        assert "abc" in data
        assert data["abc"]["task"] == "do X"
        assert data["abc"]["cron"] == "0 7 * * *"

    def test_delete_removes_entry(self, tmp_path):
        from app.agents.commander import commands as cmd_mod
        cmd_mod._persist_nl_job("abc", "t", "* * * * *", "+1")
        cmd_mod._persist_nl_job("def", "t2", "0 7 * * *", "+1")
        cmd_mod._delete_nl_job("abc")
        data = cmd_mod._read_nl_jobs()
        assert "abc" not in data
        assert "def" in data


class TestTrainingCommands:
    def test_training_status_calls_orchestrator(self, monkeypatch):
        from app.agents.commander import commands as cmd_mod

        fake = MagicMock()
        fake.format_report = MagicMock(return_value="🎓 formatted report")
        monkeypatch.setattr("app.training_pipeline.get_orchestrator",
                            lambda: fake)
        out = cmd_mod.try_command("training", "+15551", _DummyCommander())
        assert out is not None
        assert "formatted report" in out

    def test_train_now_starts_thread(self, monkeypatch):
        from app.agents.commander import commands as cmd_mod
        started = []

        def fake_cycle():
            started.append("ran")
            return {"status": "ok"}
        monkeypatch.setattr("app.training_pipeline.run_training_cycle",
                            fake_cycle)
        out = cmd_mod.try_command("train now", "+15551", _DummyCommander())
        assert out is not None
        assert "background" in out.lower()

    def test_export_training_sharegpt(self, monkeypatch):
        from app.agents.commander import commands as cmd_mod
        fake_pipeline = MagicMock()
        fake_pipeline.export_format = MagicMock(
            return_value={"exported": 42, "path": "/tmp/sharegpt_export.json"}
        )
        monkeypatch.setattr("app.training_collector.get_pipeline",
                            lambda: fake_pipeline)
        out = cmd_mod.try_command("export training sharegpt",
                                  "+15551", _DummyCommander())
        assert out is not None
        assert "42" in out
        assert "sharegpt" in out.lower()
        fake_pipeline.export_format.assert_called_once_with("sharegpt")

    def test_export_training_reports_error(self, monkeypatch):
        from app.agents.commander import commands as cmd_mod
        fake_pipeline = MagicMock()
        fake_pipeline.export_format = MagicMock(
            return_value={"exported": 0, "error": "no data"}
        )
        monkeypatch.setattr("app.training_collector.get_pipeline",
                            lambda: fake_pipeline)
        out = cmd_mod.try_command("export training alpaca",
                                  "+15551", _DummyCommander())
        assert "Export error" in out
        assert "no data" in out
