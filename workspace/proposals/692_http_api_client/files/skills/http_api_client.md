# HTTP API Client Skill

## Purpose
Enable agents to make arbitrary HTTP requests to REST APIs when web_fetch is insufficient.

## When to Use
- POST/PUT/DELETE requests (web_fetch only does GET)
- Custom headers (Authorization, Content-Type, etc.)
- JSON request bodies
- API authentication (Bearer tokens, API keys, Basic auth)
- Webhook calls
- GraphQL queries

## Implementation Pattern

Use the `code_executor` tool with Python's `requests` or `httpx` library:

```python
import requests
import json

# GET request with headers
response = requests.get(
    'https://api.example.com/data',
    headers={'Authorization': 'Bearer YOUR_TOKEN', 'Accept': 'application/json'}
)
print(response.status_code)
print(response.json())

# POST request with JSON body
response = requests.post(
    'https://api.example.com/create',
    headers={'Authorization': 'Bearer YOUR_TOKEN', 'Content-Type': 'application/json'},
    json={'name': 'test', 'value': 123}
)
print(response.json())

# PUT request
response = requests.put(
    'https://api.example.com/update/1',
    headers={'Authorization': 'Bearer YOUR_TOKEN'},
    json={'name': 'updated'}
)

# DELETE request
response = requests.delete(
    'https://api.example.com/delete/1',
    headers={'Authorization': 'Bearer YOUR_TOKEN'}
)
```

## Common Patterns

### Bearer Token Authentication
```python
headers = {'Authorization': f'Bearer {api_token}'}
```

### API Key Authentication
```python
headers = {'X-API-Key': api_key}
# or query param:
response = requests.get(f'https://api.example.com?api_key={api_key}')
```

### Basic Authentication
```python
from requests.auth import HTTPBasicAuth
response = requests.get('https://api.example.com', auth=HTTPBasicAuth('user', 'pass'))
```

### Handling Pagination
```python
page = 1
all_results = []
while True:
    response = requests.get(f'https://api.example.com/items?page={page}')
    data = response.json()
    all_results.extend(data['items'])
    if not data.get('has_more'):
        break
    page += 1
```

### Error Handling
```python
try:
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()  # Raises HTTPError for 4xx/5xx
    return response.json()
except requests.exceptions.HTTPError as e:
    print(f'HTTP Error: {e.response.status_code} - {e.response.text}')
except requests.exceptions.RequestException as e:
    print(f'Request failed: {e}')
```

### GraphQL Queries
```python
query = '''
query {
  user(id: "123") {
    name
    email
  }
}
'''
response = requests.post(
    'https://api.example.com/graphql',
    headers={'Authorization': 'Bearer TOKEN'},
    json={'query': query}
)
```

## Best Practices
1. Always set a timeout (e.g., `timeout=30`) to avoid hanging
2. Use `response.raise_for_status()` for automatic error handling
3. Check `response.status_code` before parsing JSON
4. Handle rate limiting (429 responses) with exponential backoff
5. Never hardcode API keys in code - use environment variables or parameters
6. For large responses, stream with `stream=True`

## Common APIs Reference
- GitHub: `https://api.github.com` (repos, issues, PRs)
- OpenAI: `https://api.openai.com/v1` (chat, embeddings)
- Slack: `https://slack.com/api` (messages, channels)
- Notion: `https://api.notion.com/v1` (pages, databases)
