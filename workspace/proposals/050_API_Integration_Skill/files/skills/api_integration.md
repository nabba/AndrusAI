# API Integration Best Practices

## Authentication Methods
- API keys
- OAuth 2.0
- JWT tokens

## Common Patterns
- Handling rate limits
- Pagination
- Error responses
- Retry logic

## Python Implementation
```python
import requests

headers = {'Authorization': 'Bearer YOUR_TOKEN'}
response = requests.get('https://api.example.com/data', headers=headers)
if response.status_code == 200:
    data = response.json()
else:
    handle_error(response)
```