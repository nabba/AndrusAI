import requests
import json

def fetch_real_time_data(url):
    response = requests.get(url)
    if response.status_code == 200:
        return json.loads(response.text)
    else:
        return {'error': 'Failed to fetch data'}