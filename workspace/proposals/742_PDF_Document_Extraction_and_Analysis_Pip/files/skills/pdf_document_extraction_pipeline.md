# PDF Document Extraction and Analysis Pipeline

## Overview
This skill enables systematic extraction of text, tables, figures, and metadata from PDF documents for research and analysis workflows.

## Tool Dependencies
- `code_executor` for running Python scripts
- `file_manager` for saving extracted content

## Python Libraries to Use

### Primary Extraction
```python
import fitz  # PyMuPDF - fast text extraction
import pdfplumber  # table extraction
from marker_pdf import convert_single_pdf  # high-quality OCR
```

### Supporting Libraries
```python
import pytesseract  # OCR fallback
from PIL import Image  # image handling
import camelot  # advanced table extraction
import tabula  # alternative table extraction
```

## Extraction Workflow

### Step 1: Document Assessment
```python
def assess_pdf(pdf_path):
    """Determine PDF type and best extraction method."""
    doc = fitz.open(pdf_path)
    
    info = {
        'page_count': len(doc),
        'has_text': False,
        'has_images': False,
        'is_scanned': False
    }
    
    for page in doc:
        text = page.get_text()
        if text.strip():
            info['has_text'] = True
        images = page.get_images()
        if images:
            info['has_images'] = True
    
    # If pages have images but no text, likely scanned
    if info['has_images'] and not info['has_text']:
        info['is_scanned'] = True
    
    return info
```

### Step 2: Text Extraction
```python
def extract_text(pdf_path, pages=None):
    """Extract text from PDF using PyMuPDF."""
    doc = fitz.open(pdf_path)
    text_content = []
    
    page_range = pages if pages else range(len(doc))
    
    for page_num in page_range:
        page = doc[page_num]
        text = page.get_text("text")
        text_content.append({
            'page': page_num + 1,
            'text': text,
            'char_count': len(text)
        })
    
    return text_content
```

### Step 3: Table Extraction
```python
def extract_tables(pdf_path, pages=None):
    """Extract tables using pdfplumber."""
    tables = []
    
    with pdfplumber.open(pdf_path) as pdf:
        page_range = pages if pages else range(len(pdf.pages))
        
        for page_num in page_range:
            page = pdf.pages[page_num]
            page_tables = page.extract_tables()
            
            for idx, table in enumerate(page_tables):
                tables.append({
                    'page': page_num + 1,
                    'table_index': idx,
                    'data': table,
                    'rows': len(table),
                    'cols': len(table[0]) if table else 0
                })
    
    return tables
```

### Step 4: OCR for Scanned Documents
```python
def extract_with_ocr(pdf_path, dpi=300):
    """Extract text from scanned PDFs using OCR."""
    doc = fitz.open(pdf_path)
    ocr_content = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Render page to image
        mat = fitz.Matrix(dpi/72, dpi/72)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        # Run OCR
        text = pytesseract.image_to_string(img)
        
        ocr_content.append({
            'page': page_num + 1,
            'text': text,
            'confidence': 'ocr_processed'
        })
    
    return ocr_content
```

### Step 5: Structured Output Generation
```python
def process_pdf(pdf_path, output_dir):
    """Complete PDF processing pipeline."""
    import json
    import os
    
    # Assess document
    assessment = assess_pdf(pdf_path)
    
    # Extract based on document type
    if assessment['is_scanned']:
        text = extract_with_ocr(pdf_path)
    else:
        text = extract_text(pdf_path)
    
    tables = extract_tables(pdf_path)
    
    # Compile results
    result = {
        'source': pdf_path,
        'assessment': assessment,
        'text_content': text,
        'tables': tables,
        'full_text': '\n\n'.join([p['text'] for p in text])
    }
    
    # Save outputs
    os.makedirs(output_dir, exist_ok=True)
    
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    
    with open(f"{output_dir}/{base_name}_extracted.json", 'w') as f:
        json.dump(result, f, indent=2)
    
    with open(f"{output_dir}/{base_name}_text.txt", 'w') as f:
        f.write(result['full_text'])
    
    if tables:
        import pandas as pd
        for idx, table in enumerate(tables):
            df = pd.DataFrame(table['data'][1:], columns=table['data'][0])
            df.to_csv(f"{output_dir}/{base_name}_table_{idx}.csv", index=False)
    
    return result
```

## Integration with Research Workflows

### For Policy Document Analysis
1. Extract full text for keyword searching
2. Extract tables for data verification
3. Generate markdown summary with key findings

### For Environmental Data Reports
1. Extract data tables to CSV format
2. Parse figures and charts (requires additional processing)
3. Cross-reference with other data sources

### For Estonian Document Processing
Combine with existing `estonian_document_translation_pipeline` skill:
1. Extract text from Estonian PDFs
2. Apply translation pipeline
3. Store bilingual output

## Error Handling

```python
def safe_extract(pdf_path, method='auto'):
    """Robust extraction with fallbacks."""
    try:
        if method == 'auto':
            assessment = assess_pdf(pdf_path)
            method = 'ocr' if assessment['is_scanned'] else 'text'
        
        if method == 'text':
            return extract_text(pdf_path)
        elif method == 'ocr':
            return extract_with_ocr(pdf_path)
        else:
            raise ValueError(f"Unknown method: {method}")
    
    except Exception as e:
        print(f"Primary extraction failed: {e}")
        # Fallback to OCR
        try:
            return extract_with_ocr(pdf_path)
        except Exception as e2:
            print(f"OCR fallback failed: {e2}")
            return None
```

## Quality Checks

1. **Text Coverage**: Verify extracted text covers expected content
2. **Table Integrity**: Check tables have consistent column counts
3. **Encoding Issues**: Watch for character encoding problems in non-English documents

## Performance Tips

- Use `fitz` (PyMuPDF) for fastest text extraction
- Process pages in parallel for large documents
- Cache extracted content to avoid reprocessing
- For very large PDFs, extract page ranges separately
