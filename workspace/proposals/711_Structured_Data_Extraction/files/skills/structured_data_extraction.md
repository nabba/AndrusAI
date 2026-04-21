# Structured Data Extraction

## Overview
This skill provides systematic approaches for extracting structured data from unstructured text sources.

## Extraction Categories

### 1. Contact Information

```python
import re

def extract_emails(text):
    """Extract all email addresses from text."""
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    return re.findall(pattern, text)

def extract_phone_numbers(text):
    """Extract phone numbers in various formats."""
    patterns = [
        r'\+?1?[-.]?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}',  # US format
        r'\+?\d{1,3}[-.\s]?\d{2,4}[-.\s]?\d{2,4}[-.\s]?\d{2,4}',  # International
    ]
    phones = []
    for pattern in patterns:
        phones.extend(re.findall(pattern, text))
    return phones

def extract_urls(text):
    """Extract URLs from text."""
    pattern = r'https?://[\w\-._~:/?#[@!$&\'()*+,;=%]+'
    return re.findall(pattern, text)
```

### 2. Dates and Times

```python
from dateutil import parser

def extract_dates(text):
    """Extract dates in various formats."""
    patterns = [
        r'\d{4}-\d{2}-\d{2}',  # ISO format
        r'\d{1,2}/\d{1,2}/\d{2,4}',  # US format
        r'\d{1,2}-\d{1,2}-\d{2,4}',  # Dash separated
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4}',  # Month name
    ]
    dates = []
    for pattern in patterns:
        dates.extend(re.findall(pattern, text, re.IGNORECASE))
    return dates

def parse_date_flexible(date_string):
    """Parse dates in any format using dateutil."""
    try:
        return parser.parse(date_string)
    except:
        return None
```

### 3. Prices and Currency

```python
def extract_prices(text):
    """Extract prices with currency symbols."""
    patterns = [
        r'[\$€£¥]\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?',  # Symbol before
        r'\d{1,3}(?:,\d{3})*(?:\.\d{2})?\s*(?:USD|EUR|GBP|JPY)',  # Code after
    ]
    prices = []
    for pattern in patterns:
        prices.extend(re.findall(pattern, text))
    return prices
```

### 4. Tables from Text

```python
def extract_simple_table(text, delimiter='|'):
    """Extract table data from pipe or tab delimited text."""
    lines = text.strip().split('\n')
    table = []
    for line in lines:
        if delimiter in line:
            row = [cell.strip() for cell in line.split(delimiter)]
            table.append(row)
    return table

def extract_markdown_tables(text):
    """Extract tables from markdown format."""
    pattern = r'\|(.+)\|\n\|[-| ]+\|\n((?:\|.+\|\n?)+)'
    matches = re.findall(pattern, text)
    tables = []
    for header, body in matches:
        headers = [h.strip() for h in header.split('|') if h.strip()]
        rows = []
        for line in body.strip().split('\n'):
            row = [cell.strip() for cell in line.split('|') if cell.strip()]
            if row:
                rows.append(row)
        tables.append({'headers': headers, 'rows': rows})
    return tables
```

### 5. Key-Value Pairs

```python
def extract_key_value_pairs(text, separators=[':', '=']):
    """Extract key-value pairs from text."""
    pairs = {}
    for sep in separators:
        pattern = rf'([\w\s]+)\s*{re.escape(sep)}\s*(.+)'
        matches = re.findall(pattern, text)
        for key, value in matches:
            pairs[key.strip()] = value.strip()
    return pairs
```

### 6. JSON Extraction from Text

```python
import json

def extract_json_objects(text):
    """Extract JSON objects embedded in text."""
    pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    matches = re.findall(pattern, text)
    objects = []
    for match in matches:
        try:
            obj = json.loads(match)
            objects.append(obj)
        except json.JSONDecodeError:
            continue
    return objects
```

### 7. LLM-Assisted Extraction

When regex patterns fail, use LLM with structured output:

```python
extraction_prompt = """
Extract the following information from the text below.
Return ONLY valid JSON with these keys:
- names: list of person names
- organizations: list of company/organization names
- locations: list of places mentioned
- dates: list of dates mentioned
- emails: list of email addresses
- phones: list of phone numbers

Text:
{text}

JSON:
"""
```

## Best Practices

1. **Layer your approach**: Start with regex, fall back to LLM
2. **Validate extracted data**: Check formats, ranges, consistency
3. **Handle edge cases**: Missing values, malformed data, encoding issues
4. **Normalize output**: Convert to consistent formats (lowercase, ISO dates)
5. **Preserve context**: Include surrounding text when relevant

## Common Pitfalls

- Over-matching with greedy regex (use `.*?` for non-greedy)
- Missing Unicode characters in patterns
- Not accounting for multi-line content
- Ignoring case sensitivity
- Extracting dates without timezone context

## Integration Pattern

```python
def extract_all_structured_data(text):
    """Comprehensive extraction combining all methods."""
    return {
        'emails': extract_emails(text),
        'phones': extract_phone_numbers(text),
        'urls': extract_urls(text),
        'dates': extract_dates(text),
        'prices': extract_prices(text),
        'tables': extract_markdown_tables(text),
        'key_values': extract_key_value_pairs(text),
        'json_objects': extract_json_objects(text)
    }
```
