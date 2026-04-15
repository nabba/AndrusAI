import requests
from requests.exceptions import RequestException

def enhanced_web_search(query):
    try:
        response = requests.get(f'https://api.brave.com/search?q={query}')
        response.raise_for_status()
        return response.json()
    except RequestException as e:
        print(f'Error: {e}')
        return None