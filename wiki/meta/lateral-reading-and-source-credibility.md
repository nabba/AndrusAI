---
aliases:
- lateral reading and source credibility
author: idle_scheduler.wiki_synthesis
confidence: medium
created_at: '2026-06-25T09:43:55Z'
date: '2026-06-25'
related: []
relationships: []
section: meta
source: workspace/skills/lateral_reading_and_source_credibility.md
status: active
tags:
- self-improvement
- skills
- auto-synthesised
title: Lateral Reading and Source Credibility Verification
updated_at: '2026-06-25T09:43:55Z'
version: 1
---

# Lateral Reading and Source Credibility Verification

*kb: episteme | id: skill_lateral_reading_credibility | status: active | created: 2026-05-25*

## Purpose
Apply the **SIFT method** (Stop, Investigate, Find better coverage, Trace claims) used by professional fact-checkers to evaluate web sources during research tasks. This prevents low-quality or biased sources from polluting synthesized answers — a primary driver of output quality degradation.

## When to Apply
Use this skill whenever a research task returns:
- **Statistics or numerical claims** (percentages, counts, growth rates)
- **Controversial or politically charged topics**
- **Recent events** where misinformation spreads fast
- **Scientific or medical claims**
- **Company/product claims** that may be promotional

## The SIFT Method (Adapted for AI Research Agents)

### S — Stop
Before incorporating a source, pause and ask:
- Is this source trying to persuade me of something?
- Does the headline make an extreme or surprising claim?
- Is there a date? (undated content is suspect)

**Do NOT** immediately dive into the content and accept it at face value.

### I — Investigate the Source
Before reading the article, research the source itself:
1. Run a **second web search** for `"[source name] credibility"` or `"[source name] bias"`
2. Check if NewsGuard, Media Bias / Fact Check, or similar rate it
3. Prefer: `.gov`, `.edu`, peer-reviewed journals, major established news orgs (Reuters, AP, BBC)
4. Avoid: unknown blogs, sites with excessive ads, sites whose "About" page is vague

**Credibility Tiers (for quick ranking):**
| Tier | Examples | Trust Level |
|------|----------|-------------|
| 1 (Highest) | Government data (.gov), UN/WHO reports, peer-reviewed journals | Very High |
| 2 | Established broadsheet news (Reuters, AP, BBC, NYT) | High |
| 3 | Industry reports from named firms (Gartner, McKinsey), established think tanks | Medium-High |
| 4 | Expert blogs, Wikipedia (for non-controversial facts) | Medium |
| 5 | Unknown blogs, press releases, social media posts | Low — verify before use |

### F — Find Better Coverage
If a claim seems important but the source is Tier 4 or 5:
- Search for the same claim from a Tier 1–3 source
- If no better source exists, **flag the claim as unverified** rather than treating it as fact
- Pattern: `"[key claim] site:reuters.com OR site:bbc.com OR site:gov"`

### T — Trace Claims to Their Origin
Many web articles cite other articles that cite other articles. Trace to the primary source:
- "A study found..." → find the actual study
- "According to experts..." → find the named expert and their actual quote
- "Reports suggest..." → find which report

**Anti-pattern:** Accepting a chain of secondary citations as independent confirmation. Five articles all citing the same press release = ONE source, not five.

## Practical Search Patterns

### Verify a Statistic
```
1. Note the claim: "X% of Y do Z"
2. Search: "[claim] [source type: study/report/data]"
3. Look for: primary data from Tier 1-2 sources
4. If found: use the primary source, not the article citing it
5. If not found: mark as "unverified" in synthesis
```

### Cross-Check a Factual Claim
```
1. Take the key claim
2. Search it + "fact check" OR from a different Tier 1-2 source
3. If confirmed: note confidence = HIGH
4. If contradicted: note conflict, report both, flag uncertainty
5. If only one source: confidence = MEDIUM at best
```

### Lateral Reading in Practice
Rather than reading deep into a single source:
1. **Open multiple tabs** — search for the same fact from different angles
2. **Read across** sources rather than down through one source
3. If 3+ independent Tier 1-3 sources agree → **HIGH confidence**
4. If only 1 source mentions a fact → **LOW confidence**, flag it

## Confidence Levels for Research Output

| Confidence | Criteria | How to Report |
|------------|----------|---------------|
| ✅ HIGH | 3+ independent Tier 1-3 sources agree | State as fact |
| ⚠️ MEDIUM | 1-2 good sources, no contradiction | State with light qualifier ("according to X") |
| ❓ LOW | Single weak source or conflicting sources | "Unverified: [claim]" or omit |
| ❌ SKIP | Contradicted by higher-tier sources | Do NOT include |

## Red Flags — Discard or Heavily Discount
- Article has no author name or date
- Site's primary purpose is selling something related to the claim
- Claim is only on one fringe website
- Statistics with no named source ("studies show", "experts say")
- Article was published within hours of an event with no cited sources

## Integration with Multi-Hop Research
When using query decomposition (see `query_decomposition_and_multi_hop_research.md`):
- Apply this credibility check **per sub-query result**, not just on the final synthesis
- At synthesis time, prefer Tier 1-2 sources when they exist
- Contradictions between tiers → use higher tier, note the discrepancy

## Output Annotation Convention
When reporting research findings, annotate confidence inline:
- `[✅ Reuters, 2024]` — high confidence, named source
- `[⚠️ Industry blog, unverified]` — medium confidence
- `[❓ Unverified — no primary source found]` — low confidence
- Omit ❌ claims entirely from the final answer

## Example Workflow

**Task:** "What is the current global deforestation rate?"

1. **Stop**: This is a scientific claim — verify carefully
2. **Search**: "global deforestation rate hectares per year 2024"
3. **Investigate**: Results from FAO (Tier 1), WWF (Tier 2-3), random blog (Tier 5)
4. **Use**: FAO data as primary, WWF to corroborate
5. **Trace**: FAO report → direct link to their "Global Forest Resources Assessment"
6. **Annotate**: "X million hectares/year [✅ FAO Global Forest Assessment 2023]"
7. **Flag**: Blog's different number → do NOT include (contradicted by Tier 1)

**Result**: High-quality, verifiable claim instead of a dubious statistic.
