# Structured Data Extraction Patterns

## Problem
Research and writing crews often need to extract structured data (emails, URLs, dates, prices, names) from unstructured text, HTML pages, or documents. Without dedicated extraction tools, agents must use code_executor with parsing libraries.

## Solution: Extraction Patterns via code_executor

### Email Extraction
```python
import re

text = '''Contact us at support@example.com or sales@company.org.
For urgent matters: emergency@help.desk'''

emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
print(emails)
# ['support@example.com', 'sales@company.org', 'emergency@help.desk']
```

### URL Extraction
```python
import re

text = 'Visit https://example.com or http://old-site.org/page?param=1'
urls = re.findall(r'https?://[^\s<>"]+|www\.[^\s<>"]+', text)
print(urls)
```

### Phone Number Extraction (Multiple Formats)
```python
import re

text = '''Call us: (555) 123-4567, 555.999.8888, or +1-555-222-3333'''

# North American formats
phones = re.findall(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', text)
print(phones)

# International format
intl_phones = re.findall(r'\+\d{1,3}[-.\s]?\d{1,4}[-.\s]?\d{1,4}[-.\s]?\d{1,9}', text)
print(intl_phones)
```

### Date Extraction and Normalization
```python
import re
from datetime import datetime

text = '''Events: Jan 15, 2024, 2024-02-20, 03/15/2024, March 10th 2024'''

# Extract various date formats
dates = []
date_patterns = [
    (r'\b(\d{4})-(\d{2})-(\d{2})\b', '%Y-%m-%d'),
    (r'\b(\d{2})/(\d{2})/(\d{4})\b', '%m/%d/%Y'),
    (r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b', '%b %d, %Y'),
]

for pattern, fmt in date_patterns:
    matches = re.findall(pattern, text, re.IGNORECASE)
    for match in matches:
        try:
            if isinstance(match, tuple):
                date_str = '-'.join(match) if fmt == '%Y-%m-%d' else '/'.join(match)
            else:
                date_str = match
            dates.append(date_str)
        except:
            pass

print(dates)
```

### Price/Currency Extraction
```python
import re

text = '''Prices: $19.99, €50,00, £100.50, USD 250, 99.95 USD'''

# USD prices
usd_prices = re.findall(r'\$([\d,]+\.?\d*)', text)
print(usd_prices)  # ['19.99']

# All currency amounts
all_prices = re.findall(r'([\$€£][\d,]+\.?\d*)|([\d,]+\.?\d*\s*(?:USD|EUR|GBP))', text)
print(all_prices)
```

### HTML Table Extraction
```python
import re

html = '''
<table>
  <tr><th>Name</th><th>Score</th></tr>
  <tr><td>Alice</td><td>95</td></tr>
  <tr><td>Bob</td><td>87</td></tr>
</table>
'''

# Extract table rows
rows = re.findall(r'<tr>(.*?)</tr>', html, re.DOTALL)
for row in rows:
    cells = re.findall(r'<td>(.*?)</td>', row)
    if cells:
        print(cells)
```

### JSON from Text
```python
import re
import json

text = '''Response: {"status": "ok", "data": [1, 2, 3]} and another {"error": true}'''

json_objects = re.findall(r'\{[^{}]*\}', text)
for obj in json_objects:
    try:
        data = json.loads(obj)
        print(data)
    except json.JSONDecodeError:
        pass
```

### Key-Value Pair Extraction
```python
import re

text = '''name: John Doe, email: john@test.com, phone: 555-1234, status: active'''

pairs = dict(re.findall(r'(\w+)\s*:\s*([^,]+)', text))
print(pairs)
# {'name': 'John Doe', 'email': 'john@test.com', 'phone': '555-1234', 'status': 'active'}
```

## Best Practices
1. Always handle missing/malformed data gracefully
2. Normalize extracted data to consistent formats
3. Validate extracted data against expected patterns
4. Use non-greedy quantifiers (`.*?`) to avoid over-matching
5. Test patterns with sample data before production use

## Integration with Research Crew
- Use after web_fetch to parse retrieved content
- Combine with web_scraping_using_firecrawl skill for complex pages
- Store extracted data in structured format using file_manager