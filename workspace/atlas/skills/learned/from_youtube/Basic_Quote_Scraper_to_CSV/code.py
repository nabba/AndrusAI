import requests
from bs4 import BeautifulSoup
import csv

page = requests.get('http://quotes.toscrape.com')
soup = BeautifulSoup(page.text, 'html.parser')

quotes = soup.find_all('span', class_='text')
authors = soup.find_all('small', class_='author')

file = open('scraped_quotes.csv', 'w')
writer = csv.writer(file)
writer.writerow(['Quote', 'Author'])

for quote, author in zip(quotes, authors):
    writer.writerow([quote.text, author.text])

file.close()