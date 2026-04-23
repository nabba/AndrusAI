---
aliases:
- model tool support and capability detection
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-22T16:48:56Z'
date: '2026-04-22'
related: []
relationships: []
section: meta
source: workspace/skills/model_tool_support_and_capability_detection.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Model Tool Support and Capability Detection
updated_at: '2026-04-22T16:48:56Z'
version: 1
---

# Model Tool Support and Capability Detection

*kb: episteme | id: skill_episteme_model_tool_support | status: active | usage: 0 | created: 2026-04-20T00:00:00+00:00*

# Overview

Different LLM models and providers have varying levels of support for tool/function calling. Using tools with a model that doesn't support them causes `BadRequestError` or similar API errors. This skill provides patterns for detecting model capabilities upfront and selecting appropriate models for tool-using agents.

# Model Tool Support Matrix

## Ollama Models

| Model | Tool Support | Notes |
|-------|-------------|---------|
| codestral:22b | ❌ No | Known issue: does not support tools; use only for code completion without function calls |
| codestral:latest | ⚠️ Partial | Verify version; older builds may lack tool support |
| llama3.3:latest | ✅ Yes | Full tool support |
| mistral:latest | ✅ Yes | Full tool support |
| qwen2.5-coder:latest | ✅ Yes | Full tool support |
| phi3:latest | ⚠️ Partial | Limited tool support; may require special configuration |

## OpenAI Models

| Model | Tool Support | Notes |
|-------|-------------|---------|
| gpt-4o | ✅ Yes | Full tool support |
| gpt-4-turbo | ✅ Yes | Full tool support |
| gpt-4 | ✅ Yes | Full tool support |
| gpt-3.5-turbo | ✅ Yes | Full tool support |
| o1-preview | ⚠️ Partial | Tool support limited; check API docs |
| o1-mini | ❌ No | Does not support tools; reasoning-only |

## Anthropic Models

| Model | Tool Support | Notes |
|-------|-------------|---------|
| claude-3-opus | ✅ Yes | Full tool support |
| claude-3-sonnet | ✅ Yes | Full tool support |
| claude-3-haiku | ✅ Yes | Full tool support |
| claude-2.1 | ❌ No | No native tool support |

## Local Models (via LM Studio, llama.cpp, etc.)

| Backend | Tool Support | Notes |
|---------|-------------|---------|
| llama.cpp | ⚠️ Partial | Tool support depends on build and template; may require `--tool-calls` flag |
| MLX | ⚠️ Partial | Experimental tool support |
| Transformers (HuggingFace) | ❌ No | No native tool support; requires framework integration |

# Patterns for Capability Detection

## Pattern 1: Model Name Prefix Matching

```python
TOOL-CAPABLE_MODEL_PREFIXES = [
    "gpt-",      # OpenAI models
    "claude-",   # Anthropic models
    "anthropic/",
    "openai/",
]

NON_TOOL_MODEL_KEYWORDS = [
    "o1-mini",   # OpenAI reasoning-only
    "without-tools",
    "-no-tools",
]

def model_supports_tools(model_name: str) -> bool:
    """Quick check if model likely supports tool calling."""
    model_lower = model_name.lower()
    
    # Check explicit non-tool models
    for keyword in NON_TOOL_MODEL_KEYWORDS:
        if keyword in model_lower:
            return False
    
    # Check known tool-capable prefixes
    for prefix in TOOL_CAPABLE_MODEL_PREFIXES:
        if model_lower.startswith(prefix):
            return True
    
    # Ollama special handling
    if "ollama" in model_lower:
        # Known problematic models
        if "codestral:22b" in model_lower:
            return False
        # Most recent Ollama models support tools
        return True
    
    return False  # Unknown models default to False
```

## Pattern 2: Capability Probe (Runtime Detection)

```python
import asyncio
from typing import Optional

async def probe_tool_capabilities(
    model_name: str,
    test_client,
    timeout: float = 5.0
) -> bool:
    """
    Send a minimal tool-using request to test if model actually supports tools.
    Returns True if the model successfully processes a tool call.
    """
    try:
        # Minimal test: ask model to call a no-op tool
        response = await asyncio.wait_for(
            test_client.chat_completion(
                model=model_name,
                messages=[{"role": "user", "content": "What is 2+2?"}],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "noop",
                        "description": "No operation",
                        "parameters": {"type": "object", "properties": {}}
                    }
                }],
                tool_choice="none"  # Don't actually call, just test acceptance
            ),
            timeout=timeout
        )
        # If we got here without an error, model accepts tool schema
        return True
    except Exception as e:
        # Model rejected tools or timed out
        return False
```

## Pattern 3: Provider-Level Detection

```python
PROVIDER_TOOL_SUPPORT = {
    "openai": {
        "supports_tools": True,
        "models_with_tools": [
            "gpt-4o", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"
        ],
        "models_without_tools": ["o1-mini"],
    },
    "anthropic": {
        "supports_tools": True,
        "models_with_tools": [
            "claude-3-opus", "claude-3-sonnet", "claude-3-haiku"
        ],
    },
    "ollama": {
        "supports_tools": True,
        "excluded_models": ["codestral:22b"],
        "version_threshold": "0.1.40",  # Tool support added in this version
    },
}

def provider_supports_tools(provider: str, model_name: str) -> bool:
    """Check if a specific provider+model combination supports tools."""
    config = PROVIDER_TOOL_SUPPORT.get(provider)
    if not config:
        return False
    
    if not config.get("supports_tools"):
        return False
    
    # Check explicit exclusions
    excluded = config.get("excluded_models", [])
    for excluded_model in excluded:
        if excluded_model in model_name.lower():
            return False
    
    return True
```

# Selection Strategies for Tool-Using Agents

## Strategy A: Model Filtering at Crew Creation

```python
def select_tool_capable_model(
    available_models: list[str],
    required_tools: list[dict],
    fallback: bool = True
) -> Optional[str]:
    """
    Choose a model that supports tools from the available list.
    Falls back to a known safe default if none match.
    """
    # First pass: exact matches
    for model in available_models:
        if model_supports_tools(model):
            return model
    
    if fallback:
        # Known safe defaults by provider
        FALLBACKS = {
            "ollama": "llama3.3:latest",
            "openai": "gpt-4o",
            "anthropic": "claude-3-sonnet",
        }
        for provider, fallback_model in FALLBACKS.items():
            if provider in str(available_models):
                return fallback_model
    
    return None
```

## Strategy B: Dynamic Agent Configuration

```python
class ToolAwareAgent:
    """Agent that adapts behavior based on model capabilities."""
    
    def __init__(self, model_name: str, tools: list):
        self.model_name = model_name
        self.raw_tools = tools
        
        if model_supports_tools(model_name):
            self.use_tools = True
            self.tools = tools
        else:
            self.use_tools = False
            self.tools = []
            # Convert tool schemas to natural language descriptions
            self.tool_descriptions = self._describe_tools(tools)
    
    def build_request(self, messages: list) -> dict:
        """Build API request respecting model capabilities."""
        if self.use_tools:
            return {
                "model": self.model_name,
                "messages": messages,
                "tools": self.raw_tools,
            }
        else:
            # Append tool descriptions to system prompt
            augmented = list(messages)
            if augmented and augmented[0]["role"] == "system":
                augmented[0]["content"] += f"\n\nAvailable tools (describe which to use): {self.tool_descriptions}"
            else:
                augmented.insert(0, {
                    "role": "system",
                    "content": f"You have access to: {self.tool_descriptions}"
                })
            return {
                "model": self.model_name,
                "messages": augmented,
            }
```

## Strategy C: Graceful Degradation

```python
async def safe_chat_completion(
    client,
    model_name: str,
    messages: list,
    tools: Optional[list] = None,
    **kwargs
):
    """
    Wrapper that falls back to non-tool mode if model doesn't support tools.
    Prevents BadRequestError from capability mismatches.
    """
    if not model_supports_tools(model_name):
        # Remove tools and convert to prompt instructions
        if tools:
            tool_desc = "\n".join([
                f"- {t['function']['name']}: {t['function'].get('description', 'No description')}"
                for t in tools
            ])
            system_msg = (
                "You have access to the following tools. "
                "Describe which tool you would use and why, "
                "but you cannot actually call them with this model.\n"
                f"{tool_desc}"
            )
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += "\n\n" + system_msg
            else:
                messages.insert(0, {"role": "system", "content": system_msg})
        tools = None
    
    return await client.chat_completion(
        model=model_name,
        messages=messages,
        tools=tools,
        **kwargs
    )
```

# Best Practices

## 1. Check Capabilities Before Agent Creation
Always verify model tool support before constructing tool-using agents. This prevents runtime errors and reduces retry cycles.

## 2. Maintain an Allowlist of Known-Good Models
Keep a central registry of models verified to support tools. Update it when adding new models to the system.

## 3. Log Model Selection Decisions
Record why a particular model was chosen (or excluded) to aid debugging and improve future selections.

## 4. Provide Clear Error Context
When a model fails due to missing tool support, log both the model name and the expected capability so maintainers can update the registry.

## 5. Test on Model Upgrades
When a model version changes (e.g., `codestral:22b` → `codestral:latest`), re-validate tool support as capabilities may have changed.

# References

- https://github.com/ollama/ollama/issues/5958 (codestral tool support discussion)
- https://platform.openai.com/docs/guides/function-calling (OpenAI function calling docs)
- https://docs.anthropic.com/en/docs/build-with-claude/tool-use (Anthropic tool use guide)
- Ollama model cards and capability declarations
