# Proposal #325: Fix: coding:APIConnectionError

**Type:** code
**Created:** 2026-03-30T11:28:42.600117+00:00

## Description

The error indicates a timeout when connecting to the Ollama chat service after 600 seconds. The root cause is likely either network issues or the Ollama service being unresponsive. To fix, increase the timeout setting in the litellm configuration or ensure the Ollama service is running and accessible. The exact code change needed would be to set a higher timeout value when initializing the litellm client, e.g., `litellm.set_timeout(900)` to increase the timeout to 900 seconds.

## Files

None
