# API Integration Testing

## When to Use
- Testing REST API endpoints
- Debugging authentication flows
- Validating API responses against specifications
- Rate-limited API interaction
- Webhook testing and verification

## Core Tools
- `code_executor` with Python `requests` library
- `web_fetch` for simple GET requests (no auth)

## Procedure

### 1. Basic API Request
```python
import requests

response = requests.get('https://api.example.com/data')
print(f'Status: {response.status_code}')
print(f'Headers: {dict(response.headers)}')
print(f'Body: {response.json()}')
```

### 2. Authentication Patterns

**Bearer Token:**
```python
headers = {'Authorization': f'Bearer {token}'}
response = requests.get(url, headers=headers)
```

**API Key:**
```python
headers = {'X-API-Key': api_key}
# or query param: requests.get(url, params={'api_key': api_key})
```

**Basic Auth:**
```python
from requests.auth import HTTPBasicAuth
response = requests.get(url, auth=HTTPBasicAuth('user', 'pass'))
```

**OAuth2 (Client Credentials):**
```python
# First get token
token_response = requests.post(token_url, data={
    'grant_type': 'client_credentials',
    'client_id': client_id,
    'client_secret': client_secret
})
access_token = token_response.json()['access_token']
# Then use token
headers = {'Authorization': f'Bearer {access_token}'}
```

### 3. Error Handling
```python
try:
    response = requests.get(url, timeout=30)
    response.raise_for_status()  # Raises HTTPError for 4xx/5xx
except requests.exceptions.Timeout:
    print('Request timed out')
except requests.exceptions.HTTPError as e:
    print(f'HTTP error: {e.response.status_code}')
    print(f'Response body: {e.response.text}')
except requests.exceptions.RequestException as e:
    print(f'Request failed: {e}')
```

### 4. Rate Limiting
```python
import time

def rate_limited_request(url, rate_limit_per_second=2):
    min_interval = 1.0 / rate_limit_per_second
    last_called = [0.0]
    
    def make_request():
        elapsed = time.time() - last_called[0]
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        last_called[0] = time.time()
        return requests.get(url)
    
    return make_request
```

### 5. Response Validation
```python
def validate_response(response, expected_schema):
    data = response.json()
    for key, expected_type in expected_schema.items():
        if key not in data:
            return False, f'Missing key: {key}'
        if not isinstance(data[key], expected_type):
            return False, f'Key {key} has wrong type: {type(data[key])}'
    return True, 'Valid'

# Example
schema = {'id': int, 'name': str, 'active': bool}
valid, msg = validate_response(response, schema)
```

## Common API Patterns

**Pagination:**
```python
all_results = []
page = 1
while True:
    response = requests.get(url, params={'page': page, 'per_page': 100})
    data = response.json()
    all_results.extend(data['items'])
    if not data.get('has_more') or len(data['items']) == 0:
        break
    page += 1
```

**Retry Logic:**
```python
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = requests.Session()
retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry)
session.mount('http://', adapter)
session.mount('https://', adapter)
```

## Checklist Before API Calls
- [ ] Know the base URL and endpoint path
- [ ] Have authentication credentials ready
- [ ] Understand rate limits
- [ ] Know expected response format
- [ ] Set appropriate timeout values
- [ ] Handle errors gracefully