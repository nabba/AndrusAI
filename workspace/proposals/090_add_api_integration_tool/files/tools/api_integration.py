import requests

def api_get_request(url, headers={}):
    response = requests.get(url, headers=headers)
    return response.json()