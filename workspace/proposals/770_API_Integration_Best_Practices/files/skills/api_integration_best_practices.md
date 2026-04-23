# API Integration Best Practices

## Problem
The team frequently needs to interact with external APIs (payment providers, government data portals, etc.) but lacks a consistent approach for authentication, error handling, rate limiting, and pagination. This leads to fragile integrations and repeated work.

## Solution
Standardize API interaction using the following patterns:

### 1. Choose the Right Library
- REST: `requests` (simple), `httpx` (async), `aiohttp` (async)
- GraphQL: `gql`
- SOAP: `zeep` (if needed)

### 2. Authentication
- API Key: include in headers (`Authorization: Bearer <key>` or custom header)
- OAuth2: use `requests-oauthlib` or `authlib`
- Basic Auth: `requests.auth.HTTPBasicAuth`

**Never hardcode credentials.** Use environment variables or team memory.

### 3. Rate Limiting and Retries
Implement exponential backoff:
```python
import time
import random

def safe_request(func, max_retries=3, base_delay=1):
    for attempt in range(max_retries):
        try:
            return func()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [429, 500, 502, 503, 504]:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.1)
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("Max retries exceeded")
```

### 4. Pagination
Handle both offset/limit and cursor-based pagination:
```python
def paginate(url, params, page_param='page', per_page=100):
    page = 1
    while True:
        params[page_param] = page
        resp = requests.get(url, params=params)
        data = resp.json()
        if not data['items']:
            break
        yield from data['items']
        page += 1
```

### 5. Error Handling and Logging
- Use `try/except` for network errors.
- Log request/response metadata (status, latency) for debugging.
- Raise custom exceptions for business errors (e.g., `InvalidInputError`).

### 6. Schema Validation
Validate response payloads using `pydantic` or `jsonschema` to catch API changes early.

### 7. Connection Pooling and Session Reuse
Use `requests.Session()` to reuse TCP connections and improve performance.

### 8. Timeout Configuration
Always set timeouts to avoid hanging:
```python
response = requests.get(url, timeout=(5, 30))  # (connect, read)
```

### 9. Async Considerations
For high-volume tasks, use `httpx` or `aiohttp` with `asyncio`. Ensure the environment supports async.

### 10. Security
- Verify TLS certificates (`verify=True`).
- Sanitize logs to avoid leaking secrets.
- Rotate API keys periodically.

## Example: Complete Wrapper Class
```python
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class APIClient:
    def __init__(self, base_url, api_key=None, timeout=10):
        self.base_url = base_url
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({'Authorization': f'Bearer {api_key}'})
        self.timeout = timeout
        self._configure_retries()

    def _configure_retries(self):
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

    def get(self, endpoint, params=None):
        url = self.base_url + endpoint
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()
```

## Benefits
- Reliable, maintainable integrations
- Reduced duplicated effort
- Easier onboarding for new team members
- Better monitoring and debugging