import time
import random

def call_with_retry(llm_func, *args, max_retries=1, **kwargs):
    """Wrapper for LLM calls that handles empty responses with retry logic"""
    for attempt in range(max_retries + 1):
        try:
            response = llm_func(*args, **kwargs)
            if response is None or response == '':
                raise ValueError("Empty response from LLM")
            return response
        except (ValueError, Exception) as e:
            if attempt == max_retries:
                raise
            wait_time = min(2 ** attempt + random.random(), 5)
            time.sleep(wait_time)