# Structured Data Extraction & JSON Output

## Purpose
Extract structured, machine-readable data (JSON, CSV, tables) from raw web content, HTML, or unstructured text.

## When to Use
- User asks for data comparisons, rankings, statistics, or tabular information
- Research tasks that require clean data output rather than prose
- Any task where the output needs to feed into further processing (code, charts, analysis)

## Core Techniques

### 1. Table Extraction from HTML
When web_fetch returns HTML-like content with tables:
```python
import re

def extract_tables_from_text(text):
    """Extract rows from text that appears tabular (pipe-separated, tab-separated, or whitespace-aligned)."""
    lines = text.strip().split('\n')
    tables = []
    current_table = []
    for line in lines:
        if '|' in line or '\t' in line:
            cells = [c.strip() for c in re.split(r'[|\t]', line) if c.strip()]
            if cells:
                current_table.append(cells)
        else:
            if current_table:
                tables.append(current_table)
                current_table = []
    if current_table:
        tables.append(current_table)
    return tables
```

### 2. Key-Value Pair Extraction
For pages with labeled data (specs, profiles, metadata):
```python
def extract_key_value_pairs(text, separators=[':', ' - ', '=']):
    pairs = {}
    for line in text.split('\n'):
        for sep in separators:
            if sep in line:
                key, _, value = line.partition(sep)
                key, value = key.strip(), value.strip()
                if key and value and len(key) < 60:
                    pairs[key] = value
                    break
    return pairs
```

### 3. List Extraction
```python
def extract_lists(text):
    items = []
    for line in text.split('\n'):
        line = line.strip()
        if re.match(r'^[\d]+[.)\-]\s+', line) or re.match(r'^[•\-\*]\s+', line):
            clean = re.sub(r'^[\d•\-\*.)]+\s*', '', line)
            if clean:
                items.append(clean)
    return items
```

### 4. JSON Output Template
Always structure final output as valid JSON when data extraction is the goal:
```json
{
  "source": "URL or description",
  "extracted_at": "ISO timestamp",
  "data_type": "table|key_value|list",
  "headers": ["col1", "col2"],
  "rows": [["val1", "val2"], ["val3", "val4"]],
  "metadata": {"total_rows": 2, "notes": "..."}
}
```

## Retry Strategy for Empty Outputs
1. If web_search returns nothing useful, reformulate the query: remove year qualifiers, use synonyms, try broader terms
2. If web_fetch returns empty, try browser_fetch (handles JS-heavy pages)
3. If a specific site fails, search for cached/archived versions or alternative sources
4. For future-dated queries, search for "latest", "current", "trends" instead of the future year
5. Always attempt at least 2 different query formulations before reporting failure

## Validation Checklist
- [ ] Output is valid JSON (use json.loads() to verify)
- [ ] No empty arrays/objects unless data genuinely doesn't exist
- [ ] Source URLs are included for traceability
- [ ] Data types are consistent within columns
- [ ] Null/missing values are explicitly marked as null, not empty strings
