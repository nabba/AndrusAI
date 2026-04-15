import requests

class MemorySync:
    def __init__(self):
        self.base_url = 'https://api.crewai/memory'

    def sync_memory(self, memory_update):
        response = requests.post(f'{self.base_url}/sync', json=memory_update)
        return response.json()

    def get_latest(self, query):
        response = requests.get(f'{self.base_url}/latest', params={'query': query})
        return response.json()
