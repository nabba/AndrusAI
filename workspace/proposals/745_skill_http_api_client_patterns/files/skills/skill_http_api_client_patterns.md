# HTTP API Client Patterns

## Problem
The team lacks a dedicated HTTP client tool. The `web_fetch` tool only supports GET requests and cannot handle:
- POST/PUT/DELETE/PATCH requests
- Authentication headers (Bearer tokens, API keys, Basic auth)
- Request bodies (JSON, form-data, multipart)
- Custom headers
- Session management

## Solution: Use code_executor with Python requests/httpx

The coding crew can implement HTTP client functionality using Python within `code_executor`. This skill provides patterns for common scenarios.

### Basic Patterns

#### 1. GET Request with Headers
```python
import requests

headers = {
    'Authorization': 'Bearer YOUR_TOKEN',
    'Accept': 'application/json'
}
response = requests.get('https://api.example.com/data', headers=headers)
print(response.json())
```

#### 2. POST with JSON Body
```python
import requests

payload = {'key': 'value', 'nested': {'data': 'example'}}
headers = {'Content-Type': 'application/json'}
response = requests.post('https://api.example.com/create', json=payload, headers=headers)
print(f'Status: {response.status_code}')
print(response.json())
```

#### 3. Authentication Patterns
```python
import requests
from requests.auth import HTTPBasicAuth

# Bearer Token
headers = {'Authorization': f'Bearer {api_token}'}

# API Key in Header
headers = {'X-API-Key': api_key}

# Basic Auth
response = requests.get(url, auth=HTTPBasicAuth('username', 'password'))

# API Key in Query
response = requests.get(url, params={'api_key': api_key})
```

#### 4. Retry Logic with Exponential Backoff
```python
import requests
import time

def fetch_with_retry(url, max_retries=3, backoff_factor=2):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                raise
            wait_time = backoff_factor ** attempt
            time.sleep(wait_time)
            continue
```

#### 5. File Upload (Multipart)
```python
import requests

files = {'file': ('document.pdf', open('document.pdf', 'rb'), 'application/pdf')}
data = {'description': 'Upload via API'}
response = requests.post('https://api.example.com/upload', files=files, data=data)
```

#### 6. Async with httpx (for concurrent requests)
```python
import httpx
import asyncio

async def fetch_multiple(urls):
    async with httpx.AsyncClient() as client:
        tasks = [client.get(url) for url in urls]
        responses = await asyncio.gather(*tasks)
        return [r.json() for r in responses]

# Run: asyncio.run(fetch_multiple(url_list))
```

### Error Handling Best Practices
```python
import requests
from requests.exceptions import HTTPError, ConnectionError, Timeout, RequestException

try:
    response = requests.get(url, timeout=30)
    response.raise_for_status()  # Raises HTTPError for 4xx/5xx
    data = response.json()
except HTTPError as e:
    print(f'HTTP error: {e.response.status_code}')
except ConnectionError:
    print('Connection failed')
except Timeout:
    print('Request timed out')
except RequestException as e:
    print(f'Request failed: {e}')
```

## When to Use
- Interacting with REST APIs (Stripe, Slack, GitHub, etc.)
- Testing webhooks
- Submitting forms programmatically
- Authenticated data retrieval
- Data synchronization tasks

## Dependencies
- `requests` - Standard HTTP library (usually available)
- `httpx` - For async operations (`pip install httpx`)