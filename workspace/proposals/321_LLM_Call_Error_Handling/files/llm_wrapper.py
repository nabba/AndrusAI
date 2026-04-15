import time
import random
from litellm import BadRequestError

MAX_RETRIES = 3
BASE_DELAY = 1

def robust_llm_call(llm_func, *args, **kwargs):
    """Wrapper for LLM calls with validation and retry logic"""
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        try:
            response = llm_func(*args, **kwargs)
            if response is not None and response.strip() != '':
                return response
            raise ValueError("Empty or None response from LLM")
        except (BadRequestError, ValueError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                time.sleep(delay)
            continue
    
    raise last_error if last_error else Exception("Unknown error in LLM call")