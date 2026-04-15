import json
from memory_store import memory_store, memory_retrieve

class CollaborationFramework:
    def __init__(self):
        self.shared_context = {}

    def update_context(self, key, value):
        self.shared_context[key] = value
        memory_store(text=json.dumps(self.shared_context), metadata='shared_context')

    def retrieve_context(self):
        result = memory_retrieve(query='shared_context', n_results=1)
        if result:
            self.shared_context = json.loads(result[0]['text'])
        return self.shared_context
