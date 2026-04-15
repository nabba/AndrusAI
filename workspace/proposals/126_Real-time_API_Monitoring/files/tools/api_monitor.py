import time
import requests

class APIMonitor:
    def __init__(self):
        self.latency = []
        self.errors = 0

    def track(self, func):
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                result = func(*args, **kwargs)
                self.latency.append(time.time() - start)
                return result
            except Exception as e:
                self.errors += 1
                raise e
        return wrapper

    def get_stats(self):
        return {
            'avg_latency': sum(self.latency)/len(self.latency),
            'error_rate': self.errors/(len(self.latency) + self.errors)
        }
