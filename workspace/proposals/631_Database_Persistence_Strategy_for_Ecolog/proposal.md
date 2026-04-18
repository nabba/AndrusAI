# Proposal #631: Database Persistence Strategy for Ecological Research

**Type:** skill
**Created:** 2026-04-18T18:31:35.627385+00:00

## Description

Problem: The team has no persistent storage for research data, stakeholder information, model outputs, or literature reviews. All data is ephemeral, preventing longitudinal studies, reproducible research, and team collaboration. Solution: Implement SQLite for structured data (stakeholder databases, field observations, policy assessments) combined with vector database patterns (FAISS/HNSW) for semantic search over accumulated ecological knowledge. This enables reproducible rapid assessments, historical trend analysis, and shared knowledge bases.

## Files

- `skills/database_persistence_for_ecological_research.md`
