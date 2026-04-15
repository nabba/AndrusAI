import time
import random
from typing import Optional, Callable, TypeVar, Any

T = TypeVar('T')

def validate_llm_call(
    llm_func: Callable[..., T],
    max_retries: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
    **kwargs
) -> T:
    """
    Wrapper for LLM calls with validation and retry logic.
    
    Args:
        llm_func: The LLM function to call
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
        **kwargs: Arguments to pass to llm_func
        
    Returns:
        The validated LLM response
        
    Raises:
        ValueError: If response is None/empty after max_retries
        BadRequestError: If API errors persist after max_retries
    """
    delay = initial_delay
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            response = llm_func(**kwargs)
            
            if response is None or response == '':
                raise ValueError("LLM returned None or empty response")
                
            return response
            
        except (ValueError, BadRequestError) as e:
            last_error = e
            if attempt < max_retries:
                sleep_time = min(delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
                time.sleep(sleep_time)
            continue
    
    raise last_error if last_error else ValueError("LLM call failed after retries")