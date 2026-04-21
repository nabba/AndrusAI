# Document Processing Pipeline

## Purpose
Enable agents to extract and process text from various document formats (PDF, DOCX, RTF) for research and analysis.

## Core Patterns

### 1. PDF Text Extraction (PyPDF2)
```python
# Note: Requires pypdf2 library
try:
    from PyPDF2 import PdfReader
    from io import BytesIO
    
    # If PDF bytes are available
    # reader = PdfReader(BytesIO(pdf_bytes))
    # text = ''
    # for page in reader.pages:
    #     text += page.extract_text() + '\n'
    # print(f"Extracted {len(text)} characters from {len(reader.pages)} pages")
except ImportError:
    print("PyPDF2 not available - use alternative method")
```

### 2. PDF Text Extraction (pdfplumber - More Robust)
```python
try:
    import pdfplumber
    
    # with pdfplumber.open('document.pdf') as pdf:
    #     text = ''
    #     for page in pdf.pages:
    #         text += page.extract_text() or ''
    #         
    #     # Also extract tables
    #     tables = []
    #     for page in pdf.pages:
    #         tables.extend(page.extract_tables())
except ImportError:
    print("pdfplumber not available")
```

### 3. Word Document Processing (python-docx)
```python
try:
    from docx import Document
    from io import BytesIO
    
    # If DOCX bytes are available
    # doc = Document(BytesIO(docx_bytes))
    # text = '\n'.join([para.text for para in doc.paragraphs])
    # 
    # # Extract tables
    # for table in doc.tables:
    #     for row in table.rows:
    #         row_text = [cell.text for cell in row.cells]
    #         print(' | '.join(row_text))
except ImportError:
    print("python-docx not available")
```

### 4. RTF Processing
```python
def extract_rtf_text(rtf_content):
    """Simple RTF text extraction without external libraries."""
    import re
    
    # Remove RTF control words
    text = re.sub(r'\\[a-z]+\d*\s*', '', rtf_content)
    # Remove special characters
    text = re.sub(r'[{}\\]', '', text)
    # Clean whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text

rtf_sample = r'{\rtf1\ansi Hello \\b world\\b0 !}'
print(extract_rtf_text(rtf_sample))
```

### 5. Markdown Processing
```python
import re

def extract_markdown_sections(markdown_text):
    """Extract sections from markdown by headers."""
    sections = {}
    current_header = None
    current_content = []
    
    for line in markdown_text.split('\n'):
        header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if header_match:
            if current_header:
                sections[current_header] = '\n'.join(current_content).strip()
            current_header = header_match.group(2)
            current_content = []
        else:
            current_content.append(line)
    
    if current_header:
        sections[current_header] = '\n'.join(current_content).strip()
    
    return sections

# Usage
md = '''# Main Title
Introduction text.

## Section 1
Content for section 1.

## Section 2
Content for section 2.
'''

print(extract_markdown_sections(md))
```

### 6. Content Analysis Helpers
```python
import re
from collections import Counter

def analyze_document(text):
    """Basic document analysis."""
    
    # Word frequency
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    word_freq = Counter(words).most_common(20)
    
    # Sentence count
    sentences = re.split(r'[.!?]+', text)
    sentence_count = len([s for s in sentences if s.strip()])
    
    # Paragraph count
    paragraphs = [p for p in text.split('\n\n') if p.strip()]
    
    # Average word length
    avg_word_len = sum(len(w) for w in words) / len(words) if words else 0
    
    return {
        'total_chars': len(text),
        'total_words': len(words),
        'sentences': sentence_count,
        'paragraphs': len(paragraphs),
        'avg_word_length': round(avg_word_len, 2),
        'top_words': word_freq[:10]
    }

text = "This is a sample document. It has multiple sentences. We can analyze it."
print(analyze_document(text))
```

### 7. Text Cleaning Pipeline
```python
def clean_extracted_text(text):
    """Clean text extracted from documents."""
    import re
    
    # Remove excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    
    # Fix common extraction issues
    text = re.sub(r'([a-z])-\s+([a-z])', r'\1\2', text)  # Hyphenated words
    text = re.sub(r'(\w)\s+([.,;:!?])', r'\1\2', text)  # Punctuation spacing
    
    # Remove control characters
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    
    return text.strip()
```

## Document Detection Pattern

```python
def detect_document_type(content_bytes, filename=''):
    """Detect document type from bytes and/or filename."""
    
    # Check magic bytes
    if content_bytes[:4] == b'%PDF':
        return 'pdf'
    elif content_bytes[:2] == b'PK':  # ZIP-based (DOCX, XLSX)
        if filename.endswith('.docx'):
            return 'docx'
        elif filename.endswith('.xlsx'):
            return 'xlsx'
    elif content_bytes[:5] == b'{\\rtf':
        return 'rtf'
    elif content_bytes[:8] == b'\xd0\xcf\x11\xe0':
        return 'doc'  # Old Word format
    
    # Fallback to extension
    ext = filename.lower().split('.')[-1] if '.' in filename else ''
    return ext if ext in ['pdf', 'docx', 'rtf', 'txt', 'md'] else 'unknown'
```

## Best Practices

1. **Handle encoding carefully** - Try UTF-8 first, fallback to latin-1
2. **Preserve structure** - Keep paragraph breaks and section markers
3. **Validate extraction** - Check for garbled text or encoding issues
4. **Use appropriate libraries** - pdfplumber for complex PDFs, PyPDF2 for simple ones
5. **Process in chunks** - For large documents, process page by page

## Limitations to Communicate

- Cannot directly download PDFs from URLs (would need HTTP tool)
- Requires appropriate Python libraries in sandbox
- OCR not available for scanned documents
- Complex layouts may lose formatting

## Alternative: Request HTML Version

When PDF processing fails, try:
1. Check if source has HTML/text version
2. Use web_fetch on alternate URLs
3. Request transcript or summary from source
