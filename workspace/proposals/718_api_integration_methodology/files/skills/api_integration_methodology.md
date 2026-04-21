# API Integration Methodology

## Purpose
Enable agents to interact with external REST APIs when native API tools are unavailable, using the code_executor sandbox.

## Core Pattern

### 1. Basic HTTP Request
```python
import requests

response = requests.get(
    'https://api.example.com/data',
    headers={'Accept': 'application/json'},
    timeout=30
)

if response.status_code == 200:
    data = response.json()
    print(f"Retrieved {len(data)} records")
else:
    print(f"Error: {response.status_code} - {response.text}")
```

### 2. Authenticated Requests
```python
# API Key Authentication
headers = {
    'Authorization': 'Bearer YOUR_API_KEY',
    'Accept': 'application/json'
}

# Basic Auth
from requests.auth import HTTPBasicAuth
response = requests.get(url, auth=HTTPBasicAuth('user', 'pass'))
```

### 3. Pagination Handling
```python
def fetch_all_pages(base_url, headers, max_pages=100):
    all_data = []
    page = 1
    
    while page <= max_pages:
        response = requests.get(
            base_url,
            params={'page': page, 'per_page': 100},
            headers=headers
        )
        
        if response.status_code != 200:
            break
            
        data = response.json()
        if not data:
            break
            
        all_data.extend(data)
        page += 1
    
    return all_data
```

### 4. Rate Limit Handling
```python
import time

def rate_limited_request(url, headers, rate_limit_per_second=5):
    min_interval = 1.0 / rate_limit_per_second
    last_request = [0]
    
    def make_request():
        elapsed = time.time() - last_request[0]
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        
        last_request[0] = time.time()
        return requests.get(url, headers=headers)
    
    return make_request()
```

### 5. Error Handling Pattern
```python
def safe_api_call(url, headers=None, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 429:  # Rate limited
                time.sleep(2 ** attempt)
                continue
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                return {'error': str(e)}
            time.sleep(1)
    
    return {'error': 'Max retries exceeded'}
```

## Common API Patterns

### Weather Data (Open-Meteo - No Auth)
```python
response = requests.get(
    'https://api.open-meteo.com/v1/forecast',
    params={
        'latitude': 59.4370,
        'longitude': 24.7536,
        'hourly': 'temperature_2m,precipitation'
    }
)
```

### Geocoding (Nominatim - OpenStreetMap)
```python
response = requests.get(
    'https://nominatim.openstreetmap.org/search',
    params={'q': 'Tallinn, Estonia', 'format': 'json'},
    headers={'User-Agent': 'AI-Research-Agent/1.0'}
)
```

### REST API with JSON Body
```python
response = requests.post(
    'https://api.example.com/query',
    headers={'Content-Type': 'application/json'},
    json={'query': 'SELECT * FROM data LIMIT 10'}
)
```

## Best Practices

1. **Always set timeout** - Prevent hanging on unresponsive servers
2. **Handle authentication securely** - Never log API keys
3. **Respect rate limits** - Check response headers for limits
4. **Validate responses** - Check status codes and data structure
5. **Use sessions for multiple requests** - More efficient connection pooling

## When to Use
- Fetching data from public APIs (weather, geospatial, financial)
- Integrating with SaaS platforms (GitHub, Slack, etc.)
- Querying databases with REST interfaces
- Accessing government open data portals
