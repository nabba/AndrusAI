# Structured Data & API Interaction Patterns

## Overview
This skill covers techniques for fetching, parsing, transforming, and outputting structured data across common formats and APIs.

## REST API Interaction
- **GET requests**: Use `web_fetch` for simple endpoints; use `browser_fetch` for JS-rendered API explorers
- **Authentication**: Pass API keys via query params (`?api_key=X`) or expect them in headers (requires code_executor with `requests` library)
- **Pagination**: Follow `next` links or increment `page`/`offset` parameters until empty response
- **Rate limiting**: Implement exponential backoff; respect `Retry-After` headers

## Data Formats

### JSON
- Parse with `json.loads()` in Python
- Use `jq`-style path notation to extract nested fields: `data['results'][0]['name']`
- Validate structure before processing; check for `null`/missing keys

### CSV
- Use Python `csv` module or `pandas.read_csv()`
- Always specify encoding (default UTF-8) and delimiter
- Handle headers: `DictReader` for named access

### XML/HTML
- Use `BeautifulSoup` for HTML, `xml.etree.ElementTree` for XML
- XPath expressions for precise element selection

## Data Transformation Patterns
1. **Filter**: Remove rows/entries not matching criteria
2. **Map**: Transform each entry (rename fields, convert types, compute derived values)
3. **Reduce/Aggregate**: Group by key, compute sums/averages/counts
4. **Join**: Merge datasets on shared keys
5. **Pivot**: Reshape from long to wide format or vice versa

## Structured Output Generation
- **Markdown tables**: Use `|` delimiters with header separator `|---|---|`
- **JSON output**: Always use `json.dumps(data, indent=2)` for readability
- **CSV output**: Use `csv.writer` to handle escaping/quoting automatically

## Code Patterns
```python
import json, csv, io

# Parse JSON API response
data = json.loads(response_text)
items = data.get('results', [])

# Transform to CSV
output = io.StringIO()
writer = csv.DictWriter(output, fieldnames=['name', 'value', 'date'])
writer.writeheader()
for item in items:
    writer.writerow({
        'name': item['name'],
        'value': item['metrics']['value'],
        'date': item['created_at'][:10]
    })
csv_text = output.getvalue()
```

## Best Practices
- Validate inputs before processing (check types, required fields)
- Handle encoding issues explicitly (UTF-8 BOM, Latin-1 fallbacks)
- Log transformation steps for debugging
- Use streaming for large datasets (generators, chunked reads)
- Cache API responses when doing iterative development
