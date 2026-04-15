import litellm

MAX_RETRIES = 3

def retry_llm_call(llm_call, *args, **kwargs):
    for i in range(MAX_RETRIES):
        try:
            response = llm_call(*args, **kwargs)
            if response is None or response == '':
                raise ValueError('Invalid response from LLM call - None or empty.')
            return response
        except litellm.BadRequestError as e:
            if 'OpenrouterException' in str(e):
                continue
            else:
                raise e
    raise ValueError('Max retries reached for LLM call.')
