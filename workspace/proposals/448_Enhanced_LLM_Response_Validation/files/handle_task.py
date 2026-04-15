# Enhanced LLM Response Validation

from typing import Optional
import time

def validate_llm_response(response: Optional[str]) -> bool:
    if response is None or response.strip() == '':
        return False
    return True

def execute_task_with_retry(task_function, max_retries=5):
    retry_count = 0
    while retry_count < max_retries:
        response = task_function()
        if validate_llm_response(response):
            return response
        retry_count += 1
        time.sleep(min(2 ** retry_count, 64))  # Exponential backoff up to 64 seconds
    raise ValueError('Max retries reached. Invalid or empty response from LLM.')
