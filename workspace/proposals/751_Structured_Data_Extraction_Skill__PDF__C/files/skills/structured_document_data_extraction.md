# Structured Document Data Extraction

## When to Use
Use this skill whenever a task requires pulling structured data (tables, fields, line items) out of PDFs, CSVs, Excel files, or scanned images. Especially relevant for policy research, government reports, and scientific datasets where HTML scraping is insufficient.

## Decision Tree
1. **File type = CSV/TSV** → `pandas.read_csv` with explicit `encoding` and `sep` detection via `csv.Sniffer`.
2. **File type = XLSX/XLS** → `pandas.read_excel` (engine='openpyxl' for xlsx, 'xlrd' for xls). Use `sheet_name=None` to load all sheets.
3. **File type = PDF, text-based** → `pdfplumber` for tables, `PyMuPDF (fitz)` for raw text + layout.
4. **File type = PDF, scanned/image** → `pdf2image` → `pytesseract` OCR. For Estonian set `lang='est'`.
5. **File type = DOCX** → `python-docx`.

## Canonical Code Patterns

### PDF tables (pdfplumber)
```python
import pdfplumber
rows = []
with pdfplumber.open(path) as pdf:
    for page in pdf.pages:
        for table in page.extract_tables() or []:
            rows.extend(table)
```

### PDF raw text with layout (PyMuPDF)
```python
import fitz
doc = fitz.open(path)
text = "\n".join(page.get_text("text") for page in doc)
```

### Scanned PDF OCR
```python
from pdf2image import convert_from_path
import pytesseract
pages = convert_from_path(path, dpi=300)
text = "\n".join(pytesseract.image_to_string(p, lang='est+eng') for p in pages)
```

### Excel with multiple sheets
```python
import pandas as pd
sheets = pd.read_excel(path, sheet_name=None)  # dict of DataFrames
```

### CSV with unknown delimiter/encoding
```python
import csv, pandas as pd
with open(path, 'rb') as f:
    raw = f.read(8192)
for enc in ('utf-8', 'utf-8-sig', 'cp1257', 'latin-1'):
    try:
        sample = raw.decode(enc); break
    except UnicodeDecodeError: continue
dialect = csv.Sniffer().sniff(sample)
df = pd.read_csv(path, encoding=enc, sep=dialect.delimiter)
```

## Common Pitfalls
- **Estonian text**: always try `utf-8` first, then `cp1257`. Characters õ, ä, ö, ü will be mojibake if encoding is wrong.
- **Merged cells in Excel**: pandas leaves NaN; use `df.ffill()` on the relevant axis.
- **PDF tables without ruling lines**: pdfplumber's default strategy fails. Pass `table_settings={'vertical_strategy':'text','horizontal_strategy':'text'}`.
- **Large files (>100MB)**: use `pd.read_csv(..., chunksize=50_000)` and process iteratively.
- **OCR quality**: below 300 DPI, accuracy collapses. Always convert at 300 DPI minimum.

## Required packages
`pip install pdfplumber pymupdf pandas openpyxl pytesseract pdf2image python-docx` plus system: `tesseract-ocr tesseract-ocr-est poppler-utils`.

## Output Contract
Always return data as either (a) a list of dicts with consistent keys, or (b) a pandas DataFrame. Never return raw nested lists without column headers — downstream writing/analysis crews need labeled fields.
