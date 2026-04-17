# Proposal #617: Fix: coding:TimeoutError

**Type:** code
**Created:** 2026-04-17T20:00:12.220049+00:00

## Description

Increase the max_execution_time parameter in the _execute_with_timeout method to allow more time for task completion. The current timeout of 300 seconds is insufficient. Modify the value from 300 to a higher value (e.g., 600 seconds) to accommodate the task's complexity.

## Files

None
