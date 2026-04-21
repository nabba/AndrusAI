# LLM Capability Graceful Degradation

## Problem
The team encountered a critical error:
```
BadRequestError: The model registry.ollama.ai/library/codestral:22b 
does not support the use of tools
```

This happens when a task requires tools but the selected LLM lacks tool-calling capability.

## Root Cause
- Not all LLMs support function/tool calling
- Tool support varies by model size and architecture
- Some models support tools but with limitations (context, parallel calls)

## Capability Matrix

| Model | Tool Support | Parallel Tools | Context for Tools |
|-------|-------------|----------------|-------------------|
| GPT-4o | Yes | Yes | High |
| Claude 3.5/4 | Yes | Yes | High |
| Codestral | **No** | N/A | N/A |
| Llama 3.1 70B | Yes | Limited | Medium |
| Mistral Small | No | N/A | N/A |

## Solution: Detection + Fallback Pattern

### Pattern 1: Pre-Check Capability
```python
# In agent configuration or task routing
MODEL_CAPABILITIES = {
    "codestral:22b": {"tools": False, "context": 32768},
    "gpt-4o": {"tools": True, "context": 128000, "parallel_tools": True},
    "claude-sonnet-4": {"tools": True, "context": 200000, "parallel_tools": True},
}

def can_use_tools(model_name: str) -> bool:
    return MODEL_CAPABILITIES.get(model_name, {}).get("tools", False)
```

### Pattern 2: Graceful Fallback
```python
def execute_with_fallback(task, preferred_model, fallback_model):
    if task.requires_tools and not can_use_tools(preferred_model):
        print(f"Warning: {preferred_model} doesn't support tools. Falling back to {fallback_model}")
        return execute(task, model=fallback_model)
    return execute(task, model=preferred_model)
```

### Pattern 3: Task-Based Model Selection
```python
TASK_MODEL_MAPPING = {
    "research": "claude-sonnet-4",  # Needs web_search, web_fetch tools
    "coding": "gpt-4o",  # Needs code_executor tool
    "writing": "any",  # Can use models without tools
    "analysis": "any",  # Can work without tools if needed
}

def select_model_for_task(task_type: str, requires_tools: bool):
    preferred = TASK_MODEL_MAPPING.get(task_type, "any")
    
    if requires_tools and not can_use_tools(preferred):
        # Return a model that supports tools
        return "claude-sonnet-4"  # Safe default with tool support
    
    return preferred
```

### Pattern 4: Non-Tool Alternative
```python
def execute_without_tools(task):
    """Execute a task using only the LLM's native capabilities."""
    # For models without tool support:
    # 1. Use code_executor in sandbox instead of native tools
    # 2. Generate code that performs the tool action
    # 3. Return results via code execution
    
    if task.needs_web_data:
        # Generate Python code to fetch data instead of using web_fetch tool
        code = f'''
import urllib.request
import json
response = urllib.request.urlopen("{task.url}")
data = response.read().decode()
print(data)
'''
        return execute_code(code)
```

## Implementation Checklist

1. **Add capability check before task dispatch**
   - Check if task requires tools
   - Verify selected model supports tools
   - Log mismatches for monitoring

2. **Implement fallback chain**
   - Primary model → Fallback model with tools → Non-tool approach

3. **Monitor and log**
   - Track which models fail tool tasks
   - Measure fallback frequency
   - Update capability matrix from real observations

4. **User communication**
   - Inform when fallback occurs
   - Explain why different model was used
   - Offer option to proceed without tools

## Memory Storage
Store observed capability failures:
```python
memory_store(
    text=f"Model {model_name} failed on tool-required task. Error: {error}",
    metadata="type=capability_gap,model=" + model_name
)
```

## Related Skills
- llm_capability_detection (existing)
- model_selection_patterns (proposed)
- tool_alternative_strategies (proposed)
