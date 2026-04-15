import requests

class APIIntegration:
    def __init__(self):
        self.session = requests.Session()

    def call_api(self, method, url, params=None, headers=None, auth=None, body=None):
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                auth=auth,
                json=body
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {'error': str(e)}
