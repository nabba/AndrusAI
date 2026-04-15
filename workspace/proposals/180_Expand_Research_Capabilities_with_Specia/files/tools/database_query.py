import requests

def query_database(query, database_url):
    response = requests.get(database_url, params={'query': query})
    return response.json()