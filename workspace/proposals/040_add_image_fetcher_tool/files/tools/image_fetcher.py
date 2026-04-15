import requests
from bs4 import BeautifulSoup

def fetch_images(query):
    url = f'https://www.google.com/search?q={query}&tbm=isch'
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    images = soup.find_all('img')
    return [img['src'] for img in images]

# Example usage:
# images = fetch_images('ecological biodiversity')
# print(images)