import requests

class APIFramework:
    def __init__(self, base_url, headers=None):
        self.base_url = base_url
        self.headers = headers

    def get(self, endpoint, params=None):
        response = requests.get(f'{self.base_url}/{endpoint}', headers=self.headers, params=params)
        response.raise_for_status()
        return response.json()

    def post(self, endpoint, data=None):
        response = requests.post(f'{self.base_url}/{endpoint}', headers=self.headers, json=data)
        response.raise_for_status()
        return response.json()