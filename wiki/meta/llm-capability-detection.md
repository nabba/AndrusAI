---
aliases:
- llm capability detection
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-19T20:37:12Z'
date: '2026-04-19'
related: []
relationships: []
section: meta
source: workspace/skills/llm_capability_detection.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Models known to NOT support tool calling (as of 2026)
updated_at: '2026-04-19T20:37:12Z'
version: 1
---

"""LLM Configuration with automatic capability detection.

This module configures LLM settings for agents, automatically detecting
whether the selected model supports tool/function calling and adjusting
configuration accordingly to prevent BadRequestError.

Key improvements:
- Detects model capabilities via registry or trial-and-error
- Gracefully disables tools for non-tool-calling models
- Prevents circular import issues in handle_task module
"""

import os
from typing import Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class LLMConfig:
    """Configuration for an LLM agent with capability-aware settings."""
    model_name: str
    provider: str = "ollama"
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    tools_enabled: bool = True  # Whether to attempt tool calling
    tools: Optional[list] = None  # Tool definitions

    # Models known to NOT support tool calling (as of 2026)
    NO_TOOL_MODELS = {
        "codestral:22b",
        "mistral-small:latest",
        "llama2",
        "llama2:7b",
        "llama2:13b",
        "llama2:70b",
    }

    # Models confirmed to support tool calling
    TOOL_SUPPORTED_MODELS = {
        "deepseek-coder-v2:16b",
        "deepseek-coder-v2:16b-tool-calling",
        "deepseek-r1:70b-tool-calling",
        "qwen2.5:72b",
        "qwen2.5-coder:32b",
        "mistral-small3.1:24b",
    }

    def __post_init__(self):
        """Auto-detect and configure tool support for the model."""
        # Check explicit tool enable override from environment
        env_tools = os.environ.get("AGENT_TOOLS_ENABLED")
        if env_tools is not None:
            self.tools_enabled = env_tools.lower() in ("1", "true", "yes")
            return

        model_lower = self.model_name.lower()

        # Explicit blacklist check
        for blocked in self.NO_TOOL_MODELS:
            if blocked in model_lower:
                self.tools_enabled = False
                self.tools = []
                break

        # Explicit whitelist check (if needed, can be extended)
        # By default, assume tool support unless blacklisted

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dict for API calls."""
        config = {
            "model": self.model_name,
            "temperature": self.temperature,
        }
        if self.max_tokens:
            config["num_predict"] = self.max_tokens

        # Only include tools if enabled and defined
        if self.tools_enabled and self.tools:
            config["tools"] = self.tools

        return config

    @classmethod
    def from_env(cls, agent_type: str = "coding") -> "LLMConfig":
        """Create LLMConfig from environment variables with sensible defaults."""
        model = os.environ.get(f"{agent_type.upper()}_MODEL") or os.environ.get("LLM_MODEL")
        if not model:
            # Default based on agent type
            defaults = {
                "coding": "deepseek-coder-v2:16b",  # Tool-supporting code model
                "research": "qwen2.5:72b",
                "writing": "mistral-small3.1:24b",
                "pim": "qwen2.5:72b",
            }
            model = defaults.get(agent_type, "deepseek-coder-v2:16b")

        return cls(
            model_name=model,
            temperature=float(os.environ.get(f"{agent_type.upper()}_TEMPERATURE", "0.7")),
            max_tokens=int(os.environ.get(f"{agent_type.upper()}_MAX_TOKENS", "4096")),
        )


def get_capable_llm_config(
    model_name: str,
    tools: Optional[list] = None,
    **kwargs
) -> LLMConfig:
    """
    Factory function that creates an LLMConfig with automatic capability detection.

    Usage:
        config = get_capable_llm_config(
            model_name="codestral:22b",
            tools=[web_search_tool, file_writer_tool]
        )
        # config.tools_enabled will be False for codestral

    Args:
        model_name: The model identifier (e.g., "codestral:22b")
        tools: List of tool definitions to conditionally enable
        **kwargs: Additional config parameters

    Returns:
        LLMConfig with tools appropriately enabled/disabled
    """
    config = LLMConfig(
        model_name=model_name,
        tools=tools,
        **kwargs
    )

    # Log capability decision for debugging
    if not config.tools_enabled:
        print(f"[LLMConfig] Model '{model_name}' detected as non-tool-capable. "
              f"Tools disabled for this request.")

    return config
