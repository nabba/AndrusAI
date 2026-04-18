# Proposal #632: Add PDF and Document Parsing Skill

**Type:** skill
**Created:** 2026-04-18T19:08:59.508700+00:00

## Description

Problem: The team has no capability to extract text from PDFs, DOCX, or other document formats. Users frequently need to analyze reports, papers, or business documents. The web_fetch and browser_fetch tools only work on web pages. When a user uploads or links to a PDF, the team cannot process it. Solution: Add a skill document that teaches agents to use the coding crew's Python sandbox to parse PDFs (via PyMuPDF/fitz or pdfplumber), DOCX (via python-docx), and CSV/Excel (via pandas) files, with fallback strategies and common patterns.

## Files

- `skills/document_parsing_strategies.md`
