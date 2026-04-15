import requests
import json
from typing import Dict, Any

class APIIntegrationTool:
    def execute(self, endpoint: str, method: str='GET', headers: Dict[str, str]=None, params: Dict[str, Any]=None, body: Dict[str, Any]=None) -> Dict[str, Any]:
        try:
            if method.upper() == 'GET':
                response = requests.get(endpoint, headers=headers, params=params)
            elif method.upper() == 'POST':
                response = requests.post(endpoint, headers=headers, json=body)
            else:
                raise ValueError('Unsupported HTTP method')
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {'error': str(e)}