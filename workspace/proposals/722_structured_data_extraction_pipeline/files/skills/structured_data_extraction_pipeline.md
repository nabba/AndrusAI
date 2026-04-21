# Structured Data Extraction Pipeline

## Purpose
Transform unstructured web content into clean, structured data formats (CSV, JSON, database records) for analysis and reporting.

## When to Use
- Extracting tables from web pages
- Scraping listings (products, companies, contacts)
- Converting semi-structured text to records
- Building datasets for analysis

## Extraction Workflow

### 1. Identify Target Structure
Before extracting, define the schema:
```python
# Example: Extract company data
schema = {
    'company_name': 'text',
    'revenue': 'currency',
    'employees': 'integer',
    'industry': 'text',
    'founded_year': 'year'
}
```

### 2. Choose Extraction Method

#### CSS Selector Extraction (for well-structured HTML)
```python
import requests
from bs4 import BeautifulSoup

url = 'https://example.com/companies'
response = requests.get(url)
soup = BeautifulSoup(response.content, 'html.parser')

# Extract repeating elements
companies = []
for item in soup.select('.company-card'):
    company = {
        'name': item.select_one('.name').text.strip(),
        'revenue': item.select_one('.revenue').text.strip(),
        'employees': int(item.select_one('.employees').text.strip())
    }
    companies.append(company)
```

#### LLM-Assisted Extraction (for unstructured content)
```python
# Use code_executor with LLM to parse messy data
extraction_prompt = '''
Extract company records from this text.
Return as JSON array with keys: name, revenue, employees, industry.

Text:
"""
{content}
"""
'''
```

### 3. Clean and Validate
```python
import pandas as pd
import re

def clean_currency(value):
    if not value:
        return None
    # Remove $, €, commas
    cleaned = re.sub(r'[^\d.]', '', str(value))
    return float(cleaned) if cleaned else None

def clean_year(value):
    match = re.search(r'\b(19|20)\d{2}\b', str(value))
    return int(match.group()) if match else None

# Apply cleaning
df = pd.DataFrame(companies)
df['revenue'] = df['revenue'].apply(clean_currency)
df['founded_year'] = df['founded_year'].apply(clean_year)
```

### 4. Export to Required Format
```python
# CSV
df.to_csv('output/companies.csv', index=False)

# JSON
df.to_json('output/companies.json', orient='records', indent=2)

# Ready for database insertion
records = df.to_dict('records')
```

## Integration with Team Memory
Store extracted datasets in team memory for cross-crew access:
```python
# Store schema and sample for future reference
memory_store(
    text=f"Extracted {len(df)} company records. Schema: {list(df.columns)}. Sample: {df.head(2).to_dict('records')}",
    metadata='source=web_extraction,format=csv,records={len(df)}'
)
```

## Common Patterns

### Table Extraction
```python
tables = pd.read_html(url)  # Auto-detect HTML tables
df = tables[0]  # First table
```

### Paginated Listings
```python
all_items = []
for page in range(1, max_pages + 1):
    url = f'{base_url}?page={page}'
    # Extract items
    # Add delay to avoid rate limiting
```

### Nested Data
```python
# Use recursive extraction for nested structures
def extract_nested(item, selectors):
    result = {}
    for key, selector in selectors.items():
        if isinstance(selector, dict):
            result[key] = extract_nested(item, selector)
        else:
            element = item.select_one(selector)
            result[key] = element.text.strip() if element else None
    return result
```

## Anti-Patterns to Avoid
- Extracting without rate limiting (get blocked)
- Assuming HTML structure is stable (add error handling)
- Skipping validation (garbage in, garbage out)
- Not storing extraction metadata (can't reproduce later)
