import litellm
from time import sleep

class LLMHandler:
    def __init__(self):
        self.max_retries = 3
        self.base_delay = 1
    
    def call_llm(self, prompt):
        retries = 0
        while retries < self.max_retries:
            try:
                response = litellm.completion(prompt)
                if response is None or response == '':
                    raise ValueError('Empty LLM response')
                return response
            except (litellm.BadRequestError, ValueError) as e:
                if isinstance(e, litellm.BadRequestError) and 'OpenrouterException' in str(e):
                    retries += 1
                    sleep(self.base_delay * (2 ** retries))
                else:
                    raise e
        raise Exception('Max retries reached for LLM call')