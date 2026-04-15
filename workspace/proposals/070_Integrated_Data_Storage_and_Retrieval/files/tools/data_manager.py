class DataManager:
    def __init__(self):
        self.memory = {}

    def store(self, text, metadata):
        self.memory[metadata] = text

    def retrieve(self, query, n_results=5):
        results = {k: v for k, v in self.memory.items() if query in k or query in v}
        return dict(list(results.items())[:n_results])

data_manager = DataManager()