import time
from functools import wraps

class LLMHandler:
    MAX_RETRIES = 3
    BASE_DELAY = 1.0

    @classmethod
    def validate_response(cls, response):
        if response is None or response == '':
            raise ValueError('Invalid response from LLM call - None or empty')

    @classmethod
    def retry_on_error(cls, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < cls.MAX_RETRIES:
                try:
                    response = func(*args, **kwargs)
                    cls.validate_response(response)
                    return response
                except (ValueError, APIConnectionError) as e:
                    retries += 1
                    if retries == cls.MAX_RETRIES:
                        raise e
                    delay = cls.BASE_DELAY * (2 ** retries)
                    time.sleep(delay)
        return wrapper