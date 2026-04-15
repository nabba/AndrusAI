# LLM Response Error Analysis Skill

This skill enables agents to categorize LLM errors and implement recovery strategies based on error type.

**Error Categories:**
1. Empty Responses
2. API Errors
3. Content Validation Failures

**Recovery Strategies:**
- For empty responses: Retry with adjusted parameters or fallback to alternative LLM.
- For API errors: Log error details and retry with exponential backoff.
- For content validation failures: Post-process response to meet criteria.
