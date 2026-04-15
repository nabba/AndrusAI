import time
import litellm

MAX_RETRIES = 3
BASE_DELAY = 1.0

def validate_response(response):
    if response is None or response == '':
        return False
    return True

def llm_call(params):
    attempt = 0
    while attempt < MAX_RETRIES:
        try:
            response = litellm.completion(**params)
            if validate_response(response):
                return response
            else:
                raise ValueError('Invalid response from LLM - None or empty')
        except litellm.BadRequestError as e:
            attempt += 1
            if attempt >= MAX_RETRIES:
                raise
            delay = BASE_DELAY * (2 ** (attempt - 1))
            time.sleep(delay)
    return None