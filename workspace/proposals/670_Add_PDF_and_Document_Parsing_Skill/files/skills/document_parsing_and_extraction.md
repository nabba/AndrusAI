# Document Parsing and Extraction

## Overview
This skill covers extracting text and structured data from PDF, DOCX, XLSX, and CSV files using Python in the code_executor sandbox.

## PDF Text Extraction

### Using pdfplumber (preferred for tables + text)
```python
import subprocess
subprocess.run(['pip', 'install', 'pdfplumber'], capture_output=True)

import pdfplumber

def extract_pdf_text(filepath):
    """Extract all text from a PDF file."""
    text_pages = []
    with pdfplumber.open(filepath) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text:
                text_pages.append(f"--- Page {i+1} ---\n{text}")
    return "\n\n".join(text_pages)

def extract_pdf_tables(filepath):
    """Extract tables from a PDF as lists of lists."""
    all_tables = []
    with pdfplumber.open(filepath) as pdf:
        for i, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for t_idx, table in enumerate(tables):
                all_tables.append({
                    'page': i+1,
                    'table_index': t_idx,
                    'data': table
                })
    return all_tables
```

### Using PyPDF2 (lightweight, text-only)
```python
import subprocess
subprocess.run(['pip', 'install', 'PyPDF2'], capture_output=True)

from PyPDF2 import PdfReader

def extract_with_pypdf2(filepath):
    reader = PdfReader(filepath)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text
```

## DOCX Extraction
```python
import subprocess
subprocess.run(['pip', 'install', 'python-docx'], capture_output=True)

from docx import Document

def extract_docx(filepath):
    doc = Document(filepath)
    return "\n".join([para.text for para in doc.paragraphs])
```

## CSV / Excel Parsing
```python
import subprocess
subprocess.run(['pip', 'install', 'pandas', 'openpyxl'], capture_output=True)

import pandas as pd

def load_csv(filepath):
    df = pd.read_csv(filepath)
    return df

def load_excel(filepath):
    df = pd.read_excel(filepath, engine='openpyxl')
    return df

def summarize_dataframe(df):
    """Quick summary of a dataframe."""
    return {
        'shape': df.shape,
        'columns': list(df.columns),
        'dtypes': df.dtypes.to_dict(),
        'head': df.head().to_dict(),
        'describe': df.describe().to_dict()
    }
```

## Best Practices
1. Always install packages first via subprocess since the sandbox may not have them pre-installed
2. For large PDFs, process page-by-page to avoid memory issues
3. For scanned PDFs (image-based), text extraction won't work — note this limitation to the user
4. Use pdfplumber when tables are expected; PyPDF2 for simple text
5. Always report page numbers alongside extracted content for reference
