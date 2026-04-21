# PDF Document Processing Pipeline

## Purpose
Enable research and coding crews to extract, analyze, and structure content from PDF documents for policy research, data analysis, and report generation.

## When to Use
- Policy documents, white papers, and government reports (PDF format)
- Academic papers and research publications
- Contracts, invoices, and legal documents
- Scanned documents requiring OCR
- Multi-page documents with tables and figures

## Core Pipeline Steps

### 1. PDF Text Extraction
```python
import pdfplumber

def extract_text(pdf_path: str) -> str:
    """Extract plain text from PDF."""
    text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text.append(page.extract_text() or "")
    return "\n".join(text)
```

### 2. OCR for Scanned Documents
```python
import pytesseract
from pdf2image import convert_from_path

def ocr_pdf(pdf_path: str, lang: str = "eng") -> str:
    """OCR scanned PDF pages."""
    images = convert_from_path(pdf_path)
    text = []
    for img in images:
        text.append(pytesseract.image_to_string(img, lang=lang))
    return "\n".join(text)
```

### 3. Table Extraction
```python
def extract_tables(pdf_path: str) -> list:
    """Extract tables from PDF as structured data."""
    tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_tables = page.extract_tables()
            tables.extend(page_tables)
    return tables
```

### 4. Structured Output
```python
import json

def pdf_to_structured(pdf_path: str) -> dict:
    """Convert PDF to structured JSON with metadata."""
    with pdfplumber.open(pdf_path) as pdf:
        return {
            "page_count": len(pdf.pages),
            "text": extract_text(pdf_path),
            "tables": extract_tables(pdf_path),
            "metadata": pdf.metadata
        }
```

## MCP Integration
Use MCP PDF servers for enhanced processing:
- `hello-3ubk/docu-scan`: Extract key-values and structured data
- `jina`: AI-powered document search and extraction

## Estonian Document Support
For Estonian policy documents, use OCR with Estonian language pack:
```python
ocr_pdf(document_path, lang="est")  # Estonian language code
```

## Error Handling
- Corrupted PDFs: Try alternative libraries (PyPDF2, pikepdf)
- Password-protected: Request password or skip
- Empty extraction: Fall back to OCR
- Encoding issues: Specify UTF-8 output encoding

## Quality Checks
1. Verify text extraction completeness (compare page count)
2. Validate table structure (column count consistency)
3. Cross-reference extracted data with document metadata
