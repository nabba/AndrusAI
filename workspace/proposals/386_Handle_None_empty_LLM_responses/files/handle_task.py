import litellm

MAX_RETRIES = 3

async def handle_task(task):
    retry_count = 0
    while retry_count < MAX_RETRIES:
        try:
            response = await litellm.complete(**task)
            if response and response.strip():  # Check for non-empty response
                return response
            retry_count += 1
        except Exception as e:
            if retry_count >= MAX_RETRIES:
                raise ValueError(f'LLM response invalid after {MAX_RETRIES} retries') from e
            retry_count += 1
    raise ValueError('LLM call failed to return valid response')