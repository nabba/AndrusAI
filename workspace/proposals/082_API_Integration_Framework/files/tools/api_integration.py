import requests
from typing import Dict, Union

class APIIntegrationTool:
    def __call__(self, endpoint: str, method: str='GET', headers: Dict=None, params: Dict=None, body: Dict=None) -> Union[Dict, str]:
        try:
            response = requests.request(
                method=method,
                url=endpoint,
                headers=headers,
                params=params,
                json=body
            )
            response.raise_for_status()
            return response.json() if response.headers.get('content-type') == 'application/json' else response.text
        except Exception as e:
            return f"API request failed: {str(e)}"