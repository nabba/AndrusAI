# Task Routing Guard

## Purpose
Prevent task routing failures by identifying queries that should NOT be delegated to specialist crews.

## Query Types to Handle Directly (No Crew Delegation)

### 1. Meta/Identity Questions
- "What is your name?" → Answer directly with system identity
- "What is your purpose?" → Answer directly with system description
- "What can you do?" → List available crews and capabilities
- "Who are you?" → Answer with system identity

### 2. Time/Date Questions
- "What time is it?" → Use system clock or state inability
- "What day is it?" → Use system date
- These should NEVER go to web search

### 3. Greetings and Conversational
- "Hello", "Hi", "Thanks" → Respond conversationally
- Do not route to any crew

### 4. Future Prediction Queries
- "What will be popular in 2026?" → Reframe as: "What are current trends suggesting future direction?"
- Always add disclaimer about prediction limitations
- Search for "trends" and "forecasts" rather than stated future facts

## Routing Decision Tree
1. Is it a meta/identity question? → Answer directly
2. Is it a time/date question? → Answer from system clock
3. Is it a greeting/conversational? → Respond directly
4. Is it a future prediction? → Reframe and route to research with trend-based query
5. Otherwise → Route to appropriate crew

## Failure Indicators
- If research crew returns empty output for a simple question, the routing was wrong
- If task difficulty is rated 1-2 and output fails, consider direct answering
