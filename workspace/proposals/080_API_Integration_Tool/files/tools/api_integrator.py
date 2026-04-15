import requests

def fetch_api_data(url, params=None, headers=None):
    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()
    return response.json()

# Example usage:
# data = fetch_api_data('https://api.example.com/data', {'key': 'value'})