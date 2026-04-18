"""LLM Configuration with automatic capability detection.

This module configures LLM settings for agents, automatically detecting
whether the selected model supports tool/function calling and adjusting
configuration accordingly to prevent BadRequestError.

Key improvements:
- Detects model capabilities via registry or trial-and-error
- Gracefully disables tools for non-tool-calling models
- Prevents circular import issues in handle_task module (isolated config)

Standalone module: can be safely imported by handle_task and other modules
without creating circular dependencies.
"""

import os
from typing import Optional, List, Dict, Any


class LLMConfig:
    """Configuration for an LLM agent with capability-aware settings."""
    
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
    
    def __init__(
        self,
        model_name: str,
        provider: str = "ollama",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools_enabled: bool = True,
        tools: Optional[List] = None
    ):
        self.model_name = model_name
        self.provider = provider
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tools_enabled = tools_enabled
        self.tools = tools or []
        
        # Auto-detect and configure tool support for the model
        self._configure_tool_support()
    
    def _configure_tool_support(self) -> None:
        """Auto-detect tool capability based on model name."""
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
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary for agent initialization."""
        return {
            "model": self.model_name,
            "provider": self.provider,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tools_enabled": self.tools_enabled,
            "tools": self.tools,
        }


def should_use_tools(model_name: str) -> bool:
    """Quick check if a model supports tool calling."""
    model_lower = model_name.lower()
    
    # Check blacklist
    for blocked in LLMConfig.NO_TOOL_MODELS:
        if blocked in model_lower:
            return False
    
    # Check whitelist
    for supported in LLMConfig.TOOL_SUPPORTED_MODELS:
        if supported in model_lower:
            return True
    
    # Unknown model - default to enabled (optimistic)
    return True
