# Proposal #638: Add PDF/Document Processing Capabilities via MCP

**Type:** code  
**Created:** 2026-04-18T22:55:08.696773+00:00  

## Why this is useful

The team lacks tools to extract, parse, and transform content from PDFs, Word docs, Excel, and scanned documents. This is critical for rapid ecological literature reviews, litigation support, and data integration. Adding the 'dashev88/data-transform' MCP server (found via search) provides JSON/CSV/PDF/Excel conversion capabilities. Alternatively, add 'hello-3ubk/docu-scan' for OCR and structured extraction from PDFs. Both are remote servers requiring no local install. This would significantly expand the research crew's ability to ingest diverse document formats.

## What will change

- (no file changes)

## Potential risks to other subsystems

- Requires `docker compose up -d --build gateway` to take effect

## Files touched

None

## Original description

The team lacks tools to extract, parse, and transform content from PDFs, Word docs, Excel, and scanned documents. This is critical for rapid ecological literature reviews, litigation support, and data integration. Adding the 'dashev88/data-transform' MCP server (found via search) provides JSON/CSV/PDF/Excel conversion capabilities. Alternatively, add 'hello-3ubk/docu-scan' for OCR and structured extraction from PDFs. Both are remote servers requiring no local install. This would significantly expand the research crew's ability to ingest diverse document formats.

---

**To decide:** react 👍 to the Signal notification to approve, or 👎 to reject.  
Or reply `approve 638` / `reject 638` via Signal.
