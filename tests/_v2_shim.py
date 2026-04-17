"""Shared test shim for v2 spec tests (MCP, tool plugins, NL cron, FTS5, etc.).

Use at the top of every v2 test file:

    from tests._v2_shim import install_settings_shim
    install_settings_shim()

before importing any `app.*` module. This avoids the pydantic Settings loader
trying to resolve real env vars / .env files during test collection.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def install_settings_shim(**overrides) -> None:
    """Replace app.config.get_settings with a fake. Idempotent."""
    # Ensure project root is importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    # Set minimal env so pydantic_settings doesn't choke if imported before shim
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")
    os.environ.setdefault("BRAVE_API_KEY", "brave-test")
    os.environ.setdefault("SIGNAL_BOT_NUMBER", "+10000000000")
    os.environ.setdefault("SIGNAL_OWNER_NUMBER", "+10000000001")
    os.environ.setdefault("GATEWAY_SECRET", "a" * 64)

    class _FakeSecretStr:
        def __init__(self, v):
            self._v = v

        def get_secret_value(self):
            return self._v

    class _FakeSettings:
        anthropic_api_key = _FakeSecretStr("fake-key")
        brave_api_key = _FakeSecretStr("fake-key")
        signal_bot_number = "+10000000000"
        signal_owner_number = "+10000000001"
        signal_cli_path = "/bin/true"
        signal_socket_path = "/tmp/test.sock"
        signal_http_url = ""
        signal_attachment_path = ""
        workspace_host_path = ""
        gateway_secret = _FakeSecretStr("a" * 64)
        gateway_port = 8765
        gateway_bind = "127.0.0.1"
        commander_model = "claude-sonnet-4-6"
        specialist_model = "claude-sonnet-4-6"
        cost_mode = "balanced"
        api_tier_enabled = True
        openrouter_api_key = _FakeSecretStr("")
        llm_mode = "hybrid"
        sandbox_image = "test:latest"
        sandbox_timeout_seconds = 30
        sandbox_memory_limit = "512m"
        sandbox_cpu_limit = 0.5
        self_improve_cron = "0 3 * * *"
        self_improve_topic_file = "/tmp/test_learning_queue.md"
        retrospective_cron = "0 4 * * *"
        benchmark_cron = "0 5 * * *"
        max_parallel_crews = 3
        max_sub_agents = 4
        thread_pool_size = 6
        workspace_backup_repo = ""
        workspace_sync_cron = "0 * * * *"
        conversation_history_turns = 10
        evolution_iterations = 5
        evolution_deep_iterations = 15
        evolution_auto_deploy = False
        evolution_engine = "auto"
        feedback_enabled = False
        modification_enabled = False
        modification_tier1_auto = False
        safety_auto_rollback = False
        safety_max_negative_before_rollback = 2
        canary_deploy_enabled = False
        canary_regression_tolerance = 0.05
        sandbox_evolution_enabled = False
        sandbox_parallel_count = 2
        health_monitor_enabled = False
        self_healing_enabled = False
        version_manifest_enabled = False
        bridge_enabled = False
        bridge_host = "host.docker.internal"
        bridge_port = 9100
        history_compression_enabled = True
        lifecycle_hooks_enabled = False
        tool_self_correction_enabled = False
        project_isolation_enabled = False
        subia_live_enabled = False
        subia_grounding_enabled = False
        subia_idle_jobs_enabled = False
        control_plane_enabled = False
        budget_enforcement_enabled = False
        ticket_system_enabled = False
        default_budget_per_agent_usd = 50.0
        autonomous_mode = False
        load_shed_threshold = 0
        creative_run_budget_usd = 0.10
        creative_originality_wiki_weight = 0.6
        atlas_enabled = False
        atlas_api_scout_enabled = False
        atlas_video_learning_enabled = False
        atlas_code_forge_enabled = False
        atlas_competence_tracking = False
        local_llm_enabled = True
        local_llm_base_url = "http://localhost:11434"
        ollama_base_url = "http://localhost:11434"
        ollama_max_concurrent_crews = 2
        local_model_coding = "qwen3:30b-a3b"
        local_model_architecture = "qwen3:30b-a3b"
        local_model_research = "qwen3:30b-a3b"
        local_model_writing = "qwen3:30b-a3b"
        local_model_default = "qwen3:30b-a3b"
        vetting_enabled = False
        vetting_model = "claude-sonnet-4.6"
        mem0_enabled = False
        mem0_postgres_host = "localhost"
        mem0_postgres_port = 5432
        mem0_postgres_user = "mem0"
        mem0_postgres_password = _FakeSecretStr("")
        mem0_postgres_db = "mem0"
        mem0_neo4j_url = "bolt://localhost:7687"
        mem0_neo4j_user = "neo4j"
        mem0_neo4j_password = _FakeSecretStr("")
        mem0_llm_model = "ollama/qwen3"
        mem0_embedder_model = "dummy"
        mem0_user_id = "owner"
        firebase_service_account_json = ""
        default_latitude = 60.17
        default_longitude = 24.94
        default_timezone = "Europe/Helsinki"
        embedding_dimension = 768
        embedding_refuse_fallback = True
        email_enabled = False
        email_imap_host = ""
        email_imap_port = 993
        email_smtp_host = ""
        email_smtp_port = 587
        email_address = ""
        email_password = _FakeSecretStr("")
        sec_edgar_user_agent = "test"
        structured_log_path = "/tmp/errors.jsonl"
        structured_log_max_mb = 50
        workspace_lock_timeout_s = 30
        idle_lightweight_workers = 3
        idle_heavy_time_cap_s = 600
        idle_training_interval_s = 3600
        consciousness_enabled = False
        workspace_capacity = 5
        belief_store_enabled = False
        # v2 additions
        mcp_client_enabled = False
        mcp_servers_json = ""

        @property
        def mem0_postgres_url(self):
            return ""

        @property
        def mem0_postgres_url_safe(self):
            return ""

    # Apply overrides
    s = _FakeSettings()
    for k, v in overrides.items():
        setattr(s, k, v)

    import app.config as config_mod
    config_mod.get_settings = lambda _s=s: _s
    config_mod.get_anthropic_api_key = lambda: "fake-key"
    config_mod.get_brave_api_key = lambda: "fake-key"
    config_mod.get_gateway_secret = lambda: "a" * 64
    config_mod.get_openrouter_api_key = lambda: ""
    return s
