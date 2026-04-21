# REST API Client Patterns

## Problem
The team has `web_fetch` for basic HTTP GET requests but lacks robust API testing capabilities for:
- Authentication (OAuth 2.0, API keys, Bearer tokens)
- POST/PUT/DELETE operations
- Rate limit handling
- Pagination traversal
- Request signing and HMAC

## Solution: Multi-Method Approach

### Method 1: MCP Postman Server
Use the Postman MCP server for structured API testing:

```python
# Via MCP tools (after mcp_add_server)
# Tools available: postman_run_request, postman_create_collection, etc.
```

### Method 2: Code Executor with HTTP Client
For complex API interactions, use code_executor with the `httpx` library:

```python
import httpx
import os

# Basic GET with headers
response = httpx.get(
    "https://api.example.com/data",
    headers={"Authorization": f"Bearer {os.environ.get('API_KEY')}"}
)
print(response.json())
```

```python
# POST with JSON body
import httpx

response = httpx.post(
    "https://api.example.com/create",
    json={"name": "test", "value": 42},
    headers={"X-API-Key": os.environ.get("API_KEY")}
)
print(response.status_code, response.json())
```

```python
# Pagination handling
import httpx

def fetch_all_pages(base_url, max_pages=100):
    results = []
    page = 1
    
    while page <= max_pages:
        response = httpx.get(
            base_url,
            params={"page": page, "per_page": 100},
            timeout=30.0
        )
        data = response.json()
        
        if not data.get("items"):
            break
        
        results.extend(data["items"])
        page += 1
        
        # Rate limit handling
        if "x-ratelimit-remaining" in response.headers:
            remaining = int(response.headers["x-ratelimit-remaining"])
            if remaining < 5:
                import time
                time.sleep(60)  # Wait before rate limit hits
    
    return results
```

```python
# OAuth 2.0 flow
import httpx

def get_oauth_token(token_url, client_id, client_secret):
    response = httpx.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }
    )
    return response.json()["access_token"]

def api_call_with_oauth(api_url, token_url, client_id, client_secret):
    token = get_oauth_token(token_url, client_id, client_secret)
    response = httpx.get(
        api_url,
        headers={"Authorization": f"Bearer {token}"}
    )
    return response.json()
```

## Common Patterns

### Authentication Types
1. **API Key in Header**: `headers={"X-API-Key": key}`
2. **Bearer Token**: `headers={"Authorization": f"Bearer {token}"}`
3. **Basic Auth**: `auth=(username, password)`
4. **OAuth 2.0**: Get token first, then use as Bearer

### Error Handling
```python
import httpx

try:
    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()  # Raises for 4xx/5xx
except httpx.TimeoutException:
    print("Request timed out")
except httpx.HTTPStatusError as e:
    print(f"HTTP error: {e.response.status_code}")
```

### Rate Limiting
- Check `X-RateLimit-Remaining` header
- Implement exponential backoff on 429 responses
- Use `Retry-After` header when available

## Integration with Team
- Research crew: Use for API-based data sources
- Coding crew: Build API integrations in code_executor
- Store API keys in environment variables, never hardcode
