import team_memory_store, team_memory_retrieve
def store_memory(text, metadata=''):
    team_memory_store.store(text, metadata)
def retrieve_memory(query, n_results=5):
    return team_memory_retrieve.retrieve(query, n_results)