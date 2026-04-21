# Structured Data Extraction Skill

## Overview
Extract structured data from PDFs, documents, and other file formats. This skill covers multiple extraction approaches depending on document complexity.

## Tool Requirements
- `code_executor`: For Python-based extraction (PyPDF2, pdfplumber, tabula-py)
- Optional MCP: `hello-3ubk/docu-scan` for OCR-based extraction

## Extraction Methods by Document Type

### 1. Text-Based PDFs
```python
import pdfplumber

with pdfplumber.open('document.pdf') as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        print(text)
```

### 2. Table Extraction from PDFs
```python
import pdfplumber

with pdfplumber.open('report.pdf') as pdf:
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            # table is a list of lists (rows)
            for row in table:
                print(row)
```

### 3. Multi-page Table Consolidation
```python
import pdfplumber
import pandas as pd

all_tables = []
with pdfplumber.open('financial_report.pdf') as pdf:
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            df = pd.DataFrame(table[1:], columns=table[0])
            all_tables.append(df)

final_df = pd.concat(all_tables, ignore_index=True)
```

### 4. Form Data Extraction
```python
from PyPDF2 import PdfReader

reader = PdfReader('form.pdf')
fields = reader.get_fields()
for field_name, field_data in fields.items():
    print(f"{field_name}: {field_data.get('/V')}")
```

### 5. OCR for Scanned Documents
If the PDF is a scanned image (no extractable text), use the docu-scan MCP:
```
mcp_add_server(name="hello-3ubk/docu-scan", query="pdf document processing", env_vars="")
```
Then extract key-values or full structured data.

## Extraction Patterns

### Invoice Data
- Look for: Invoice number, date, vendor, line items, totals
- Pattern: Usually in header (invoice #, date) and tables (line items)
- Validate: Sum of line items matches total

### Contract/Agreement
- Look for: Parties, dates, terms, signatures
- Pattern: Named sections, definition blocks
- Extract: Key dates, party names, monetary values

### Research Paper
- Look for: Title, authors, abstract, references
- Pattern: Structured sections, numbered citations
- Extract: Metadata, key findings, citations

### Financial Report
- Look for: Period, figures, categories
- Pattern: Tables with row/column headers
- Extract: Numerical data into structured format

## JSON Output Template
Always structure extracted data as JSON for consistency:
```json
{
  "document_type": "invoice|contract|report|other",
  "extraction_date": "ISO-8601 timestamp",
  "confidence": "high|medium|low",
  "metadata": {
    "source_file": "filename.pdf",
    "pages_processed": 5
  },
  "extracted_fields": {
    "field_name": "value",
    "...": "..."
  },
  "tables": [
    {
      "headers": ["col1", "col2"],
      "rows": [["val1", "val2"]]
    }
  ],
  "raw_text": "Full extracted text if needed"
}
```

## Error Handling
- Try pdfplumber first (best for structured PDFs)
- Fall back to PyPDF2 if pdfplumber fails
- Use OCR only when text extraction returns empty
- Report low confidence for ambiguous extractions
