#!/usr/bin/env python3
"""
Model capability registry and selection logic.
Prevents assigning tool-using tasks to models that don't support tool calling.
"""

from typing import Dict, Set, Optional, Tuple
from dataclasses import dataclass

@dataclass
class ModelCapabilities:
    """Capability flags for a model."""
    supports_tools: bool = False
    supports_vision: bool = False
    supports_json_mode: bool = False
    max_context: int = 8192
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0

class ModelRegistry:
    """
    Central registry of model capabilities.
    Used to prevent capability mismatches before task execution.
    """
    
    # Known models and their capabilities
    # Source: Model provider documentation and empirical testing
    KNOWN_MODELS: Dict[str, ModelCapabilities] = {
        # Ollama models
        "registry.ollama.ai/library/codestral:22b": ModelCapabilities(
            supports_tools=False,  # Known issue: BadRequestError when tools used
            max_context=8192,
        ),
        "registry.ollama.ai/library/codestral:7b": ModelCapabilities(
            supports_tools=False,
            max_context=8192,
        ),
        "registry.ollama.ai/library/llama3.2:3b": ModelCapabilities(
            supports_tools=True,
            max_context=8192,
        ),
        "registry.ollama.ai/library/llama3.2:70b": ModelCapabilities(
            supports_tools=True,
            max_context=8192,
        ),
        # OpenAI models
        "openai:gpt-4o": ModelCapabilities(
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            max_context=128000,
            cost_per_1k_input=0.005,
            cost_per_1k_output=0.015,
        ),
        "openai:gpt-4o-mini": ModelCapabilities(
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            max_context=128000,
            cost_per_1k_input=0.00015,
            cost_per_1k_output=0.0006,
        ),
        "openai:gpt-4-turbo": ModelCapabilities(
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            max_context=128000,
            cost_per_1k_input=0.01,
            cost_per_1k_output=0.03,
        ),
        # Anthropic models
        "anthropic:claude-3-opus": ModelCapabilities(
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            max_context=200000,
            cost_per_1k_input=0.015,
            cost_per_1k_output=0.075,
        ),
        "anthropic:claude-3-sonnet": ModelCapabilities(
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            max_context=200000,
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
        ),
        "anthropic:claude-3-haiku": ModelCapabilities(
            supports_tools=True,
            supports_vision=True,
            supports_json_mode=True,
            max_context=200000,
            cost_per_1k_input=0.00025,
            cost_per_1k_output=0.00125,
        ),
    }
    
    @classmethod
    def get_capabilities(cls, model_name: str) -> ModelCapabilities:
        """
        Get capabilities for a model. If unknown, use conservative defaults.
        
        Args:
            model_name: Model identifier string
            
        Returns:
            ModelCapabilities object
        """
        # Try exact match first
        if model_name in cls.KNOWN_MODELS:
            return cls.KNOWN_MODELS[model_name]
        
        # Try normalized matching (strip registry prefix)
        # e.g., "codestral:22b" -> check if any known model ends with this
        normalized = model_name.split("/")[-1] if "/" in model_name else model_name
        for known, caps in cls.KNOWN_MODELS.items():
            if known.endswith(normalized):
                return caps
        
        # Unknown model: assume minimal capabilities
        # Conservative default: tools NOT supported (safest assumption)
        return ModelCapabilities(
            supports_tools=False,  # Assume no tools until proven otherwise
            max_context=8192,  # Conservative default
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
        )
    
    @classmethod
    def supports_tool_calling(cls, model_name: str) -> bool:
        """Quick check if model supports tool/function calling."""
        return cls.get_capabilities(model_name).supports_tools
    
    @classmethod
    def get_supported_models_for_tools(cls) -> Set[str]:
        """Get set of model names that support tool calling."""
        return {
            name for name, caps in cls.KNOWN_MODELS.items()
            if caps.supports_tools
        }
    
    @classmethod
    def select_model_with_tool_support(cls, preferred_models: list) -> Optional[str]:
        """
        From a list of preferred models, select the first that supports tools.
        
        Args:
            preferred_models: Ordered list of model preferences
            
        Returns:
            First tool-capable model, or None if none available
        """
        for model in preferred_models:
            if cls.supports_tool_calling(model):
                return model
        return None


def validate_model_for_task(model_name: str, requires_tools: bool) -> Tuple[bool, Optional[str]]:
    """
    Validate that a model is suitable for a given task.
    
    Args:
        model_name: Model to validate
        requires_tools: Whether the task needs tool/function calling
        
    Returns:
        (is_valid, fallback_model_or_none)
    """
    if not requires_tools:
        return True, None
    
    if ModelRegistry.supports_tool_calling(model_name):
        return True, None
    
    # Model doesn't support tools but task requires them
    # Suggest a fallback
    fallback = ModelRegistry.select_model_with_tool_support(
        ["openai:gpt-4o-mini", "anthropic:claude-3-haiku", "openai:gpt-4o"]
    )
    return False, fallback
