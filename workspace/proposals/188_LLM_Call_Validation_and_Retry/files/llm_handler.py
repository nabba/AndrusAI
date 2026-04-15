import time

def handle_llm_call(llm_function, *args, max_retries=3, initial_delay=1):
    for attempt in range(max_retries):
        try:
            response = llm_function(*args)
            if response is None or response == '':
                raise ValueError('Invalid response from LLM call - None or empty.')
            return response
        except ValueError as e:
            if attempt == max_retries - 1:
                raise e
            time.sleep(initial_delay * (2 ** attempt))
        except BadRequestError as e:
            if attempt == max_retries - 1:
                raise e
            time.sleep(initial_delay * (2 ** attempt))
    return None