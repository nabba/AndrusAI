# PDF Processing

## Required Libraries
- PyPDF2 (basic text extraction)
- pdfminer.six (advanced extraction)
- pdfplumber (table extraction)

## Key Techniques
```python
# Basic text extraction with PyPDF2
from PyPDF2 import PdfReader

def extract_text(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text()
    return text

# Advanced processing with pdfplumber
import pdfplumber

def extract_tables(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        tables = []
        for page in pdf.pages:
            tables.extend(page.extract_tables())
    return tables
```

## Common Challenges
- OCR requirements for scanned PDFs
- Handling complex layouts
- Preserving table structures