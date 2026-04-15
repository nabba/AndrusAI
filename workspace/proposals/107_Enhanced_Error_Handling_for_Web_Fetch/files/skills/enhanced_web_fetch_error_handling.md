# Enhanced Web Fetch Error Handling

## Problem
Web fetching can fail due to timeouts, incomplete data, or unexpected content types.

## Solution
Implement retry mechanisms, handle timeouts gracefully, and validate content types before processing.

### Steps:
1. **Retry Mechanism**: Implement exponential backoff for retries.
2. **Timeout Handling**: Set a timeout limit and handle exceptions accordingly.
3. **Content Validation**: Check the content type before proceeding with data extraction.

### Example:
```python
import requests
from time import sleep
def fetch_with_retries(url, max_retries=3, timeout=10):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise e
            sleep(2 ** attempt)
```