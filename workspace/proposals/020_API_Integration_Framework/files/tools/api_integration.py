import requests
from typing import Dict, Any

class APIIntegrator:
    def __init__(self, base_url: str, auth: Dict[str, str] = None):
        self.base_url = base_url
        self.auth = auth
    
    def make_request(self, endpoint: str, method: str = 'GET', params: Dict[str, Any] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"
        response = requests.request(method, url, params=params, auth=self.auth)
        return response.json()