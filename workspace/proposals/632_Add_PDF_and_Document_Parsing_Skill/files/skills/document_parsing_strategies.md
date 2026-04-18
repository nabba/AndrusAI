# Document Parsing Strategies

## Overview
When users provide PDF, DOCX, Excel, or CSV files for analysis, use the coding crew's Python sandbox to extract and process content.

## PDF Extraction
- **Primary method**: Use `PyMuPDF` (fitz) — fast, reliable, preserves layout
  ```python
  import fitz
  doc = fitz.open('document.pdf')
  text = ''
  for page in doc:
      text += page.get_text()
  ```
- **Alternative**: Use `pdfplumber` for table-heavy PDFs
  ```python
  import pdfplumber
  with pdfplumber.open('document.pdf') as pdf:
      for page in pdf.pages:
          tables = page.extract_tables()
          text = page.extract_text()
  ```
- **Fallback**: If neither library is installed, use `pip install PyMuPDF` in the sandbox first.

## DOCX Extraction
```python
from docx import Document
doc = Document('file.docx')
for para in doc.paragraphs:
    print(para.text)
```

## CSV/Excel Processing
```python
import pandas as pd
df = pd.read_csv('data.csv')  # or pd.read_excel('data.xlsx')
print(df.describe())
print(df.head(20))
```

## Best Practices
1. Always check file size before loading into memory
2. For large PDFs (>50 pages), process page-by-page and summarize incrementally
3. For scanned PDFs (image-based), note that OCR (pytesseract) may be needed
4. When extracting tables, prefer pdfplumber over PyMuPDF
5. Always handle encoding errors gracefully with `errors='replace'`
6. Return structured summaries rather than raw dumps for large documents
