import requests
from typing import Dict, Any

class APIIntegrationTool:
    def __init__(self):
        self.auth_tokens = {}

    def call_api(self, endpoint: str, method: str='GET', params: Dict[str, Any]=None, headers: Dict[str, str]=None, auth_type: str=None) -> Dict[str, Any]:
        try:
            if auth_type and auth_type in self.auth_tokens:
                headers = headers or {}
                headers.update({'Authorization': f'Bearer {self.auth_tokens[auth_type]}'})

            response = requests.request(method, endpoint, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {'error': str(e)}