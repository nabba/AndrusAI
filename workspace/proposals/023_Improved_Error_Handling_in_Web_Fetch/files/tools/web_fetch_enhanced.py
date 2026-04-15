import requests
from requests.exceptions import RequestException

def enhanced_web_fetch(url):
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.text
    except RequestException as e:
        return f'Error fetching URL: {e}'