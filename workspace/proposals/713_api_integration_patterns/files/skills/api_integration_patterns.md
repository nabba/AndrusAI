# API Integration Patterns

## Problem
The team needs to interact with REST APIs, webhooks, and external services. `web_fetch` only retrieves static content and cannot handle POST requests, authentication headers, or API-specific requirements.

## Solution: Using code_executor for API Calls

### Basic GET Request
```python
import urllib.request
import json

url = "https://api.example.com/data"
headers = {"Accept": "application/json"}

req = urllib.Request(url, headers=headers)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read().decode())
    print(data)
```

### POST Request with JSON Body
```python
import urllib.request
import json

url = "https://api.example.com/submit"
data = json.dumps({"key": "value"}).encode('utf-8')

req = urllib.Request(url, data=data, headers={
    'Content-Type': 'application/json',
    'Authorization': 'Bearer YOUR_TOKEN'
})
with urllib.request.urlopen(req) as response:
    result = json.loads(response.read())
    print(result)
```

### Retry Logic with Exponential Backoff
```python
import urllib.request
import json
import time

def api_call_with_retry(url, max_retries=3, base_delay=1):
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read())
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)
    return None
```

### Common Authentication Patterns

**Bearer Token:**
```python
headers = {'Authorization': f'Bearer {token}'}
```

**API Key in Header:**
```python
headers = {'X-API-Key': api_key}
```

**Basic Auth:**
```python
import base64
credentials = base64.b64encode(f'{user}:{password}'.encode()).decode()
headers = {'Authorization': f'Basic {credentials}'}
```

### Error Handling Best Practices
1. Always set timeouts (avoid hanging)
2. Check HTTP status codes
3. Handle rate limiting (429 responses)
4. Parse error messages from API response body
5. Use retry logic for transient failures

### When to Use vs web_fetch
- **Use API patterns:** POST/PUT/DELETE requests, authenticated endpoints, rate-limited APIs, streaming responses
- **Use web_fetch:** Static content, simple page retrieval, no authentication needed