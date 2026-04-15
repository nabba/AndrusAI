from selenium import webdriver
from bs4 import BeautifulSoup
import time
import random

class WebScraper:
    def __init__(self):
        self.driver = webdriver.Chrome()

    def scrape_page(self, url):
        self.driver.get(url)
        time.sleep(random.uniform(1, 3))
        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        return soup

    def close(self):
        self.driver.quit()

# Example usage:
# scraper = WebScraper()
# page_content = scraper.scrape_page('https://example.com')
# scraper.close()