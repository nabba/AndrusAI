# Structured Data and API Response Processing

## Overview
This skill covers efficient patterns for fetching, parsing, comparing, and presenting structured data from REST APIs, JSON endpoints, CSV files, and web tables.

## Key Patterns

### 1. REST API Consumption
When a task requires data from a known API:
- **Identify the endpoint** first using web_search if needed
- **Use code_executor** to make requests with proper headers, pagination, and error handling
- **Cache responses** when multiple queries hit the same API

```python
import requests
import json

def fetch_api(url, headers=None, params=None):
    try:
        resp = requests.get(url, headers=headers or {}, params=params or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {'error': str(e)}
```

### 2. Data Comparison Framework
When comparing two entities (e.g., AI models, products, services):
1. Define comparison dimensions upfront (features, performance, cost, etc.)
2. Gather data for each entity separately
3. Normalize units and formats before comparing
4. Present as a structured table or matrix
5. Provide a summary with clear winner per dimension

### 3. JSON/CSV Parsing
- For JSON: Use `json.loads()` and navigate with key paths
- For CSV: Use `csv.DictReader` for named column access
- For nested data: Flatten with recursive extraction
- Always validate data types before computation

### 4. Web Table Extraction
When data is in HTML tables:
```python
from html.parser import HTMLParser
# Or use: pandas.read_html(url) for quick extraction
import pandas as pd
tables = pd.read_html(url)
```

### 5. Error Handling Patterns
- **Rate limiting**: Implement exponential backoff (1s, 2s, 4s, 8s)
- **Missing data**: Mark as 'N/A' rather than failing entire task
- **Timeout**: Set 30s per request, retry once on timeout
- **Auth failures**: Report clearly, suggest alternative data sources

### 6. Output Formatting
- Use markdown tables for comparisons
- Use bullet lists for feature enumerations
- Include data freshness timestamps
- Cite the source URL for each data point

## When to Apply
- Task asks to "compare", "list", "analyze" entities with factual data
- Task requires pulling numbers, statistics, or metrics
- Task involves processing uploaded CSV/JSON files
- Research tasks that are slow due to unstructured scraping when APIs exist

## Common Pitfalls
1. **Don't scrape when an API exists** — always check for API first
2. **Don't present raw JSON** — always format for readability
3. **Don't assume data freshness** — always note when data was retrieved
4. **Don't skip pagination** — many APIs return partial results by default
