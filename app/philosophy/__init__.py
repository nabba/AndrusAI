"""
Philosophy RAG Knowledge Base — Humanist philosophical grounding for agents.

Dedicated vector store, ingestion pipeline, and CrewAI tool for retrieving
philosophical texts.  Separate from the enterprise KB — different chunking
strategy, metadata schema, and access patterns.

Safety: This KB is READ-ONLY for agents.  Only the dashboard API can add or
remove texts.  This prevents self-improving agents from gaming their own
value alignment markers (DGM safety constraint).
"""
