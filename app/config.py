from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    anthropic_api_key: str
    brave_api_key: str

    signal_bot_number: str
    signal_owner_number: str
    signal_cli_path: str = "/opt/homebrew/bin/signal-cli"
    signal_socket_path: str = "/tmp/signal-cli.sock"

    gateway_secret: str
    gateway_port: int = 8765
    gateway_bind: str = "127.0.0.1"

    commander_model: str = "claude-opus-4-6"
    specialist_model: str = "claude-sonnet-4-6"

    sandbox_image: str = "crewai-sandbox:latest"
    sandbox_timeout_seconds: int = 30
    sandbox_memory_limit: str = "512m"
    sandbox_cpu_limit: float = 0.5

    self_improve_cron: str = "0 3 * * *"
    self_improve_topic_file: str = "workspace/skills/learning_queue.md"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
