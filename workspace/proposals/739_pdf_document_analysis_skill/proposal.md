# Proposal #739: pdf_document_analysis_skill

**Type:** skill  
**Created:** 2026-04-21T13:59:06.282484+00:00  

## Why this is useful

PROBLEM: The team has no PDF parsing capability. Research tasks involving policy papers, academic articles, and official documents (especially relevant to Estonian policy research skills) will fail when encountering PDF sources. The web_fetch tool cannot extract content from PDF files. SOLUTION: Add a skill that provides methodology for PDF analysis: 1) Use MCP pdf-generator-api server for PDF operations, 2) Python code patterns using PyMuPDF/pdfplumber in code_executor, 3) Text extraction strategies for scanned documents requiring OCR, 4) Table extraction techniques for data-heavy reports, 5) Metadata analysis for document verification. This enables the research crew to process the full spectrum of document types encountered in policy research.

## What will change

- Modifies `skills/pdf_document_analysis_skill.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/pdf_document_analysis_skill.md`

## Original description

PROBLEM: The team has no PDF parsing capability. Research tasks involving policy papers, academic articles, and official documents (especially relevant to Estonian policy research skills) will fail when encountering PDF sources. The web_fetch tool cannot extract content from PDF files. SOLUTION: Add a skill that provides methodology for PDF analysis: 1) Use MCP pdf-generator-api server for PDF operations, 2) Python code patterns using PyMuPDF/pdfplumber in code_executor, 3) Text extraction strategies for scanned documents requiring OCR, 4) Table extraction techniques for data-heavy reports, 5) Metadata analysis for document verification. This enables the research crew to process the full spectrum of document types encountered in policy research.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 739` / `reject 739` via Signal.
