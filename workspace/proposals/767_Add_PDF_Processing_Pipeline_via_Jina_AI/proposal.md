# Proposal #767: Add PDF Processing Pipeline via Jina AI MCP Server

**Type:** skill  
**Created:** 2026-04-22T23:58:37.482015+00:00  

## Why this is useful

Problem: Multiple skills reference Estonian white paper/PDF analysis (estonian_pdf_white_paper_analysis, estonian_pdf_improvement_suggestion_workflow, estonian_policy_document_critique), yet current tools lack any native PDF text extraction or parsing capability. Web_fetch fails on PDF URLs, and browser_fetch won't render binary documents. The research crew's document-heavy Estonian policy work is severely hampered. Solution: Add the Jina AI MCP server (4,402 installs) which provides ground AI-powered PDF reading and structured extraction. It can fetch PDFs from URLs and return extracted text ready for analysis.

Required actions:
1. Run: mcp_add_server with name='jina', query='pdf processing document text extraction', env_vars='' (no auth required for basic usage)
2. Create skill file: sk

## What will change

- (no file changes)

## Potential risks to other subsystems

- No subsystems identified as affected

## Files touched

None

## Original description

Problem: Multiple skills reference Estonian white paper/PDF analysis (estonian_pdf_white_paper_analysis, estonian_pdf_improvement_suggestion_workflow, estonian_policy_document_critique), yet current tools lack any native PDF text extraction or parsing capability. Web_fetch fails on PDF URLs, and browser_fetch won't render binary documents. The research crew's document-heavy Estonian policy work is severely hampered. Solution: Add the Jina AI MCP server (4,402 installs) which provides ground AI-powered PDF reading and structured extraction. It can fetch PDFs from URLs and return extracted text ready for analysis.

Required actions:
1. Run: mcp_add_server with name='jina', query='pdf processing document text extraction', env_vars='' (no auth required for basic usage)
2. Create skill file: skills/pdf_processing_with_jina_mcp.md documenting:
   - Pattern: jina.read(url=PDF_URL) → markdown text
   - Workflow: fetch PDF → extract → chunk → summarize → critique
   - Estonian language handling: pre-extract then route through translation pipeline
3. Patch existing Estonian PDF analysis skills to use jina.read before language processing
4. Add fallback: if extraction.confidence < 0.7, trigger estonian_document_translation_pipeline for pre-translation

Impact: Unblocks all Estonian document analysis tasks, enables proper white paper critique methodology, and fills the critical gap between raw PDFs and the team's document-heavy skills.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 767` / `reject 767` via Signal.
