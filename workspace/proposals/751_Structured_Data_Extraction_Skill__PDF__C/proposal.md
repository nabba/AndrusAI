# Proposal #751: Structured Data Extraction Skill (PDF, CSV, Excel)

**Type:** skill  
**Created:** 2026-04-21T16:41:11.674565+00:00  

## Why this is useful

Problem: The team has many Estonian policy research skills and web scraping tools, but no standardized approach to extracting structured data from PDFs, CSVs, or Excel files — which are the dominant formats for government policy documents, research datasets, and statistical reports. Current tools (web_fetch, web_search) return HTML/text only; code_executor can run Python but lacks a documented playbook. Solution: A skill file that documents a reliable pipeline using pdfplumber/PyMuPDF for PDFs (including table extraction), pandas for CSV/Excel, and OCR fallback (pytesseract) for scanned documents. Includes error-handling patterns for malformed files, encoding issues (relevant for Estonian: UTF-8 vs Windows-1257), and memory-efficient streaming for large files.

## What will change

- Modifies `skills/structured_document_data_extraction.md`

## Potential risks to other subsystems

- Uncategorised (skills): impact scope unclear

## Files touched

- `skills/structured_document_data_extraction.md`

## Original description

Problem: The team has many Estonian policy research skills and web scraping tools, but no standardized approach to extracting structured data from PDFs, CSVs, or Excel files — which are the dominant formats for government policy documents, research datasets, and statistical reports. Current tools (web_fetch, web_search) return HTML/text only; code_executor can run Python but lacks a documented playbook. Solution: A skill file that documents a reliable pipeline using pdfplumber/PyMuPDF for PDFs (including table extraction), pandas for CSV/Excel, and OCR fallback (pytesseract) for scanned documents. Includes error-handling patterns for malformed files, encoding issues (relevant for Estonian: UTF-8 vs Windows-1257), and memory-efficient streaming for large files.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 751` / `reject 751` via Signal.
