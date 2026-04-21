# PDF Document Processing

## Overview
This skill enables extraction and processing of content from PDF documents.

## Key Libraries
- **PyMuPDF (fitz)**: Fast text and image extraction, supports encrypted PDFs
- **pdfplumber**: Excellent for table extraction and complex layouts
- **pypdf**: Pure Python, good for basic text extraction
- **pytesseract + pdf2image**: OCR for scanned documents

## Common Patterns

### 1. Basic Text Extraction
```python
import fitz  # PyMuPDF

def extract_text(pdf_path):
    doc = fitz.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text
```

### 2. Table Extraction
```python
import pdfplumber

def extract_tables(pdf_path):
    tables = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_tables = page.extract_tables()
            tables.extend(page_tables)
    return tables
```

### 3. OCR for Scanned PDFs
```python
from pdf2image import convert_from_path
import pytesseract

def ocr_pdf(pdf_path):
    images = convert_from_path(pdf_path)
    text = ""
    for image in images:
        text += pytesseract.image_to_string(image)
    return text
```

### 4. Extract Images from PDF
```python
def extract_images(pdf_path, output_dir):
    doc = fitz.open(pdf_path)
    for page_num, page in enumerate(doc):
        images = page.get_images()
        for img_index, img in enumerate(images):
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            with open(f"{output_dir}/page{page_num}_img{img_index}.png", "wb") as f:
                f.write(image_bytes)
```

### 5. Extract Metadata
```python
def get_metadata(pdf_path):
    doc = fitz.open(pdf_path)
    metadata = doc.metadata
    return {
        "title": metadata.get("title"),
        "author": metadata.get("author"),
        "subject": metadata.get("subject"),
        "keywords": metadata.get("keywords"),
        "creator": metadata.get("creator"),
        "producer": metadata.get("producer"),
        "creation_date": metadata.get("creationDate"),
        "page_count": doc.page_count
    }
```

## Best Practices
1. Always close document handles to prevent memory leaks
2. Use pdfplumber for complex table layouts
3. Check if PDF is scanned (no text layer) before attempting OCR
4. Handle encrypted PDFs with appropriate passwords
5. For large PDFs, process page-by-page to manage memory

## Error Handling
```python
def safe_extract(pdf_path):
    try:
        return extract_text(pdf_path)
    except Exception as e:
        if "password" in str(e).lower():
            return "Error: PDF is password protected"
        elif "corrupt" in str(e).lower():
            return "Error: PDF file is corrupted"
        else:
            return f"Error: {str(e)}"
```

## Integration with MCP Servers
Consider using the `hello-3ubk/docu-scan` MCP server for OCR-based PDF extraction when dealing with scanned documents or complex layouts.
