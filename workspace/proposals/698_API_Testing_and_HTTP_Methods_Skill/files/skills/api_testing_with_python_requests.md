# API Testing with Python Requests

## Problem
The team's web_fetch tool only supports GET requests. Many automation tasks require:
- POST/PUT/DELETE operations
- Authentication (API keys, Bearer tokens, Basic Auth)
- Custom headers and content types
- JSON payload handling
- Multipart form uploads

## Solution: Use code_executor with `requests` library

### Basic POST Request
```python
import requests
import json

response = requests.post(
    'https://api.example.com/data',
    json={'key': 'value'},
    headers={'Content-Type': 'application/json'}
)
print(f'Status: {response.status_code}')
print(f'Response: {response.json()}')
```

### Authenticated Request with Bearer Token
```python
import requests

api_token = 'your-api-token'
response = requests.get(
    'https://api.example.com/protected',
    headers={'Authorization': f'Bearer {api_token}'}
)
print(response.json())
```

### PUT/DELETE Operations
```python
# Update resource
response = requests.put(
    'https://api.example.com/resource/123',
    json={'updated_field': 'new_value'}
)

# Delete resource
response = requests.delete('https://api.example.com/resource/123')
```

### Handling Errors and Timeouts
```python
try:
    response = requests.post(
        'https://api.example.com/data',
        json={'key': 'value'},
        timeout=30
    )
    response.raise_for_status()  # Raise exception for 4xx/5xx
    print(response.json())
except requests.exceptions.Timeout:
    print('Request timed out')
except requests.exceptions.HTTPError as e:
    print(f'HTTP error: {e}')
except requests.exceptions.RequestException as e:
    print(f'Request failed: {e}')
```

### Multipart Form Upload
```python
files = {'file': ('document.pdf', open('document.pdf', 'rb'), 'application/pdf')}
response = requests.post(
    'https://api.example.com/upload',
    files=files,
    headers={'Authorization': 'Bearer TOKEN'}
)
```

### Webhook Integration Example
```python
import requests

# Send notification to webhook
webhook_url = 'https://hooks.slack.com/services/XXX/YYY/ZZZ'
payload = {
    'text': 'Task completed successfully!',
    'channel': '#notifications'
}
response = requests.post(webhook_url, json=payload)
```

## Common Use Cases
1. **GitHub API**: Create issues, push files, manage repos
2. **Slack/Discord Webhooks**: Send notifications
3. **SaaS Integrations**: CRM updates, ticket systems
4. **Data Submission**: Push processed data to endpoints
5. **Authentication Flows**: OAuth token exchange

## Checklist Before API Calls
- [ ] Verify endpoint URL and method (GET/POST/PUT/DELETE)
- [ ] Check authentication requirements
- [ ] Prepare headers (Content-Type, Authorization)
- [ ] Format payload correctly (JSON, form-data, multipart)
- [ ] Set appropriate timeout
- [ ] Handle potential errors gracefully
