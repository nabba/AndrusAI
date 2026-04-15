import litellm

class LLMCaller:
    def __init__(self):
        self.max_retries = 3

    def call(self, prompt):
        for attempt in range(self.max_retries):
            try:
                response = litellm.completion(prompt)
                if response is None or response.strip() == '':
                    raise ValueError('Empty or None response from LLM call')
                return response
            except (litellm.BadRequestError, ValueError) as e:
                if attempt == self.max_retries - 1:
                    raise e
                continue