# Data Triangulation & Verification Protocol

## Problem
Single-source reliability leads to hallucinations or outdated lead data in B2B research.

## Solution: The Triangulation Method
1. **Primary Source**: Identify the initial data point (e.g., LinkedIn).
2. **Secondary Source**: Verify via company official website or regulatory filings.
3. **Tertiary Source**: Validate via a third-party signal (e.g., news article, press release, or specialized DB).

## Verification Logic
- **Confirmed**: 3/3 sources agree.
- **Probable**: 2/3 sources agree.
- **Unverified**: 1/3 source or conflicting data.

## Implementation
When performing structured research, the agent must create a verification table listing all three sources for every critical entity identified.