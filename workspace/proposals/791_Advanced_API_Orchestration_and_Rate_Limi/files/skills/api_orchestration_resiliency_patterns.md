# API Orchestration and Resiliency Patterns

## Patterns for High-Volume Agentic Workflows

### 1. Circuit Breaker Pattern
Prevent system exhaustion by stopping requests to a failing API endpoint for a cooldown period.

### 2. Exponential Backoff with Jitter
Avoid 'thundering herd' problems when hitting rate limits by increasing wait times randomly.

### 3. Fallback Provider Logic
Implementation of a priority queue: 
- Primary: High-performance model (e.g., GPT-4o/Claude 3.5)
- Secondary: Cost-effective model (e.g., GPT-4o-mini/Claude Haiku)
- Tertiary: Open-source fallback via OpenRouter.