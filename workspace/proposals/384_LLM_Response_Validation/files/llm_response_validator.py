def validate_response(response):
    if response is None or response.strip() == '':
        raise ValueError('LLM response is None or empty')
    return response