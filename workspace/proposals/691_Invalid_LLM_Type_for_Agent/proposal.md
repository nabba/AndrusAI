# Proposal #691: Invalid LLM Type for Agent

**Type:** code  
**Created:** 2026-04-20T18:12:11.393755+00:00  

## Why this is useful

Diagnosis: The Agent initialization failed because the `llm` parameter was provided an instance of `_CreditFailoverLLM`, which is not a valid string or `BaseLLM` instance as required by the Agent's Pydantic schema.

Fix: The `_CreditFailoverLLM` class in `app.llm_factory` is being passed directly to the CrewAI Agent, but it does not inherit from the required `BaseLLM` (LangChain) class. To fix, ensure `_CreditFailoverLLM` inherits from the appropriate base class (e.g., `BaseChatModel` or `BaseLLM`) and implements the required abstract methods, or modify the initialization logic to extract the underlying valid LLM instance from the wrapper before passing it to the Agent.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

Diagnosis: The Agent initialization failed because the `llm` parameter was provided an instance of `_CreditFailoverLLM`, which is not a valid string or `BaseLLM` instance as required by the Agent's Pydantic schema.

Fix: The `_CreditFailoverLLM` class in `app.llm_factory` is being passed directly to the CrewAI Agent, but it does not inherit from the required `BaseLLM` (LangChain) class. To fix, ensure `_CreditFailoverLLM` inherits from the appropriate base class (e.g., `BaseChatModel` or `BaseLLM`) and implements the required abstract methods, or modify the initialization logic to extract the underlying valid LLM instance from the wrapper before passing it to the Agent.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 691` / `reject 691` via Signal.
