# Structured Data Processing & File Conversion Patterns

## Overview
Reusable patterns for processing structured data and converting between file formats using Python in the code_executor sandbox.

## CSV Processing
```python
import csv
import json

# Read CSV
with open('input.csv', 'r') as f:
    reader = csv.DictReader(f)
    rows = list(reader)

# Write CSV
with open('output.csv', 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
```

## JSON Processing
```python
import json

# Read/write JSON
with open('data.json', 'r') as f:
    data = json.load(f)

with open('output.json', 'w') as f:
    json.dump(data, f, indent=2)

# Flatten nested JSON
def flatten(obj, prefix=''):
    items = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.update(flatten(v, key))
        else:
            items[key] = v
    return items
```

## Markdown to PDF Conversion
```python
# Method 1: Using markdown + pdfkit (if wkhtmltopdf available)
import subprocess

def md_to_html(md_text):
    """Convert markdown to HTML manually for basic formatting."""
    import re
    html = md_text
    # Headers
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    # Bold and italic
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)
    # Paragraphs
    html = re.sub(r'\n\n', '</p><p>', html)
    return f'<html><body style="font-family:Arial;margin:40px;"><p>{html}</p></body></html>'

# Method 2: Using fpdf2 (pure Python, no external deps)
from fpdf import FPDF

def text_to_pdf(text, output_path):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font('Helvetica', size=11)
    for line in text.split('\n'):
        pdf.cell(0, 7, line, new_x='LMARGIN', new_y='NEXT')
    pdf.output(output_path)
```

## Excel Processing
```python
# Using openpyxl
from openpyxl import Workbook, load_workbook

# Read
wb = load_workbook('input.xlsx')
ws = wb.active
data = [[cell.value for cell in row] for row in ws.iter_rows()]

# Write
wb = Workbook()
ws = wb.active
for row in data:
    ws.append(row)
wb.save('output.xlsx')
```

## Data Analysis Patterns
```python
# Quick statistics without pandas
def describe(values):
    n = len(values)
    s = sorted(values)
    return {
        'count': n,
        'mean': sum(values) / n,
        'min': s[0],
        'max': s[-1],
        'median': s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2,
    }

# With pandas if available
try:
    import pandas as pd
    df = pd.read_csv('data.csv')
    print(df.describe())
    print(df.groupby('category').agg({'value': ['mean', 'sum']}))
except ImportError:
    pass  # Fall back to csv module
```

## Key Guidelines
- Always check which libraries are available with `pip list` first
- For PDF generation, prefer `fpdf2` (pure Python) over wkhtmltopdf
- For large files, use streaming/chunked processing
- Always write output to `/app/workspace/output/` directory
- Validate data types before processing to avoid runtime errors
