# Proposal #578: Fix: research:ConnectionError

**Type:** code
**Created:** 2026-04-07T04:01:37.235767+00:00

## Description

The error occurs due to a timeout when trying to connect to the OpenAI API. The root cause is likely network latency or server unavailability. The fix would be to increase the timeout value in the OpenAI API connection settings. In the `_call_completions` method, add a `timeout` parameter to the API request with a higher value (e.g., 30 seconds).

## Files

None
