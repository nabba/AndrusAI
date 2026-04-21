# PDF Document Analysis Skill

## Purpose
Enable extraction and analysis of content from PDF documents for research and policy analysis tasks.

## When to Use
- Source URLs point to .pdf files
- Policy documents, academic papers, or official reports need analysis
- Tabular data embedded in PDFs requires extraction
- Document metadata needs verification

## Approaches

### Method 1: Python Libraries in Code Executor
```python
import fitz  # PyMuPDF

# Extract text from PDF
def extract_pdf_text(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text()
    return text

# Extract tables
import pdfplumber

def extract_tables(pdf_bytes):
    tables = []
    with pdfplumber.open(pdf_bytes) as pdf:
        for page in pdf.pages:
            tables.extend(page.extract_tables())
    return tables
```

### Method 2: MCP PDF Server
- Use `mcp_search_servers` with query "pdf" to find available servers
- Add `pdf-generator-api/mcp-server` for PDF operations
- Enables: PDF generation, form filling, HTML-to-PDF conversion

### Method 3: Browser Rendering
- Use `browser_fetch` for JavaScript-heavy PDF viewers
- Some sites embed PDFs in JS viewers that require rendering

## OCR for Scanned Documents
```python
import pytesseract
from pdf2image import convert_from_bytes

def ocr_pdf(pdf_bytes):
    images = convert_from_bytes(pdf_bytes)
    text = ""
    for img in images:
        text += pytesseract.image_to_string(img)
    return text
```

## Metadata Extraction
```python
def get_pdf_metadata(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return {
        "author": doc.metadata.get("author"),
        "title": doc.metadata.get("title"),
        "created": doc.metadata.get("creationDate"),
        "modified": doc.metadata.get("modDate"),
        "pages": len(doc)
    }
```

## Workflow Integration
1. Research crew identifies PDF source
2. Download via web_fetch or code_executor requests
3. Extract text/tables using appropriate method
4. Store extracted content in memory for analysis
5. Writing crew uses extracted data for reports

## Common Issues
- **Encrypted PDFs**: Require password or decryption tools
- **Large files**: Process page-by-page to avoid memory issues
- **Complex layouts**: May require manual section identification
- **Non-Latin scripts**: Ensure appropriate OCR language packs installed
