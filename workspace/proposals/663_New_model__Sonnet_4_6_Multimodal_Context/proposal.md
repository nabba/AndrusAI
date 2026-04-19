# Proposal #663: New model: Sonnet 4.6 Multimodal Context Window Expansion

**Type:** skill  
**Created:** 2026-04-19T04:47:32.928899+00:00  

## Why this is useful

Tech radar discovered: Sonnet 4.6 Multimodal Context Window Expansion

Anthropic's Sonnet 4.6 features 1M token context window with multimodal support (text, images, audio, video, PDFs), enabling richer agent reasoning over complex documents.

Recommended action: Evaluate Sonnet 4.6 for agents requiring document analysis or multimodal input processing; assess context window advantages for long-running reasoning tasks.

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Tech radar discovered: Sonnet 4.6 Multimodal Context Window Expansion

Anthropic's Sonnet 4.6 features 1M token context window with multimodal support (text, images, audio, video, PDFs), enabling richer agent reasoning over complex documents.

Recommended action: Evaluate Sonnet 4.6 for agents requiring document analysis or multimodal input processing; assess context window advantages for long-running reasoning tasks.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 663` / `reject 663` via Signal.

---
## Migration note

Closed 2026-04-19T20:38:14.328710+00:00 by `scripts/migrate_tech_radar_proposals.py`.

Tech-radar model discoveries no longer flow through the proposal system — they plant stubs in `control_plane.discovered_models` (`source='tech_radar'`) so the standard benchmark + promotion pipeline in `app/llm_discovery.py` can pick them up.

No stub planted — the LLM could not map this title to a confirmed OpenRouter slug (likely hallucinated, unreleased, or not an individual model).
