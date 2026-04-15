# LLM Call Validation and Retry

## Hypothesis
Adding validation and retry logic for LLM calls will reduce error rates by handling empty/None responses and BadRequestErrors gracefully

## Description
Implement robust validation and retry logic for LLM calls to handle empty/None responses and BadRequestErrors. This includes:
1. Validating response is not None/empty before processing
2. Implementing exponential backoff retry for BadRequestErrors
3. Adding clear error messages for debugging
4. Limiting retry attempts to prevent infinite loops

## Code
```python
import time
import random
from typing import Optional

def call_llm_with_retry(
    llm_func: callable,
    max_retries: int = 3,
    initial_delay: float = 1.0
) -> Optional[dict]:
    """
    Wrapper for LLM calls with validation and retry logic.
    
    Args:
        llm_func: Function that makes the LLM call
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
    
    Returns:
        dict: LLM response if successful, None otherwise
    """
    retry_count = 0
    delay = initial_delay
    
    while retry_count <= max_retries:
        try:
            response = llm_func()
            
            # Validate response is not None/empty
            if response and isinstance(response, dict) and response.get('content'):
                return response
                
            raise ValueError("Invalid response from LLM call - None or empty")
            
        except (ValueError, BadRequestError) as e:
            retry_count += 1
            if retry_count > max_retries:
                print(f"Max retries ({max_retries}) exceeded for LLM call")
                return None
                
            # Exponential backoff with jitter
            delay = min(initial_delay * (2 ** (retry_count - 1)) + random.uniform(0, 1), 30)
            print(f"Retry {retry_count}/{max_retries} in {delay:.2f}s - Error: {str(e)}")
            time.sleep(delay)
    
    return None
```