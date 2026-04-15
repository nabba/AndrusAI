from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

class EnhancedWebInteraction:
    def __init__(self):
        self.driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))

    def fetch_dynamic_content(self, url):
        self.driver.get(url)
        return self.driver.page_source

    def execute_javascript(self, script):
        return self.driver.execute_script(script)

    def close(self):
        self.driver.quit()