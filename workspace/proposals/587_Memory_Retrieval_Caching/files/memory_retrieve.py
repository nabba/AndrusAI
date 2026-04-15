import time

class MemoryRetriever:
    def __init__(self):
        self.cache = {}
        self.cache_timeout = 300  # 5 minutes in seconds

    def retrieve(self, query, n_results=5):
        current_time = time.time()
        if query in self.cache and current_time - self.cache[query]['timestamp'] < self.cache_timeout:
            return self.cache[query]['results']
        else:
            results = self._fetch_from_api(query, n_results)
            self.cache[query] = {'results': results, 'timestamp': current_time}
            return results

    def _fetch_from_api(self, query, n_results):
        # Placeholder for actual API call
        return []
