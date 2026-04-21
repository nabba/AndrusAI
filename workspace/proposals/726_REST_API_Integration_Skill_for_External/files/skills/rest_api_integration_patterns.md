# REST API Integration Patterns

## Purpose
Enable agent teams to reliably integrate with external REST APIs using standardized patterns for authentication, error handling, and data extraction.

## Core Patterns

### 1. Authentication Methods
```python
# Bearer Token
headers = {"Authorization": f"Bearer {API_KEY}"}

# API Key in Header
headers = {"X-API-Key": API_KEY}

# Basic Auth
from requests.auth import HTTPBasicAuth
auth = HTTPBasicAuth(username, password)

# OAuth 2.0 (Client Credentials)
import requests
token_response = requests.post(token_url, {
    "grant_type": "client_credentials",
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET
})
access_token = token_response.json()["access_token"]
```

### 2. Retry Logic with Exponential Backoff
```python
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def api_call(url, headers=None, params=None):
    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json()
```

### 3. Pagination Handling
```python
def get_all_pages(base_url, headers=None, params=None, data_key="data"):
    """Handle cursor-based or offset pagination automatically."""
    all_data = []
    params = params or {}
    next_cursor = None
    
    while True:
        if next_cursor:
            params["cursor"] = next_cursor  # or "page" for offset-based
        
        response = requests.get(base_url, headers=headers, params=params)
        data = response.json()
        all_data.extend(data.get(data_key, []))
        
        next_cursor = data.get("next_cursor") or data.get("pagination", {}).get("next")
        if not next_cursor:
            break
    
    return all_data
```

### 4. Error Handling Pattern
```python
def safe_api_call(url, method="GET", **kwargs):
    """Robust API call with comprehensive error handling."""
    try:
        response = requests.request(method, url, **kwargs)
        
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            raise RateLimitError(f"Rate limited. Retry after {retry_after}s")
        
        response.raise_for_status()
        return {"success": True, "data": response.json(), "status": response.status_code}
        
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timed out", "retry": True}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "Connection failed", "retry": True}
    except requests.exceptions.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text}"}
    except json.JSONDecodeError:
        return {"success": False, "error": "Invalid JSON response"}
```

### 5. Response Validation
```python
def validate_response(data, required_fields):
    """Validate API response contains expected fields."""
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Response missing required fields: {missing}")
    return True
```

## Common API Patterns by Service Type

| Service Type | Auth Method | Pagination | Rate Limit |
|--------------|-------------|------------|------------|
| Social Media | OAuth 2.0 | Cursor | High (429 common) |
| Data APIs | API Key | Offset/Cursor | Moderate |
| Enterprise | Bearer/OAuth | Cursor | Variable |
| Government | API Key/None | Offset | Low |

## Integration Checklist
- [ ] Identify authentication method from API docs
- [ ] Test authentication with simple endpoint
- [ ] Implement retry logic for transient failures
- [ ] Handle pagination for list endpoints
- [ ] Add response validation for critical fields
- [ ] Store credentials securely (env vars, never in code)
- [ ] Document rate limits and quotas

## Example: Complete Integration
```python
import os
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

API_KEY = os.environ.get("SERVICE_API_KEY")
BASE_URL = "https://api.example.com/v1"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def fetch_resource(resource_id):
    headers = {"Authorization": f"Bearer {API_KEY}"}
    response = requests.get(f"{BASE_URL}/resources/{resource_id}", headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()
```
