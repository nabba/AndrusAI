---
aliases:
- error patterns resolution
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-04-23T00:34:43Z'
date: '2026-04-23'
related: []
relationships: []
section: meta
source: workspace/skills/error_patterns_resolution.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Error Patterns and Resolutions
updated_at: '2026-04-23T00:34:43Z'
version: 1
---

# Error Patterns and Resolutions

## Current Error Distribution
- `coding:BadRequestError`: 16 occurrences
- `handle_task:ImportError`: 7 occurrences  
- `research:RuntimeError`: 7 occurrences
- `coding:TimeoutError`: 4 occurrences
- `coding:RuntimeError`: 4 occurrences

## Top Error: BadRequestError (16x)
**Root Cause**: The model `registry.ollama.ai/library/codestral:22b` does not support tool/function calling, causing BadRequestError when agents attempt to use tools.

**Resolution Strategy**: Implement model capability detection and automatic fallback:
1. Detect model capabilities before task assignment
2. Route tool-using tasks to tool-capable models
3. Provide graceful degradation for non-tool models

## Critical Issue: ImportError in handle_task.py (7x)
**Root Cause**: Circular import dependencies between task handling modules.

**Resolution Strategy**: 
- Restructure imports to break circular dependencies
- Move shared utilities to separate module
- Use lazy imports where appropriate

## 402 Insufficient Credits Errors
**Observed in**: [pim], [coding] crews
**Error**: `Insufficient credits`
**Resolution**: Implement credit monitoring and graceful degradation:
- Pre-execution credit check
- Fallback to local/cheaper models when credits low  
- Clear error messaging for quota exceeded

## Connection/Timeout Errors
**Pattern**: ConnectionError, TimeoutError across multiple crews
**Resolution**: Implement robust retry logic with exponential backoff
- Configurable retry attempts (default: 3)
- Exponential backoff with jitter
- Circuit breaker pattern for persistent failures

## Self-Healing Rate: 88%
The system shows strong self-healing capability. Focus on preventing known errors rather than adding recovery complexity.
