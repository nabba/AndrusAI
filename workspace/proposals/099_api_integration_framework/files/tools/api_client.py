import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class APIClient:
    def __init__(self, base_url):
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1)
        self.session.mount('https://', HTTPAdapter(max_retries=retries))
    
    def get_data(self, endpoint, params=None):
        try:
            response = self.session.get(f"{self.base_url}/{endpoint}", params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            # Error handling logic...
            return None