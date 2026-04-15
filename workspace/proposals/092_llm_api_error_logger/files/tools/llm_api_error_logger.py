from datetime import datetime
import json
from pathlib import Path

class LLMErrorLogger:
    def __init__(self, log_file='llm_errors.json'):
        self.log_file = Path(log_file)
        if not self.log_file.exists():
            self.log_file.write_text('[]')

    def log_error(self, error_type, error_message, context):
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'error_type': error_type,
            'error_message': error_message,
            'context': context
        }
        log_data = json.loads(self.log_file.read_text())
        log_data.append(log_entry)
        self.log_file.write_text(json.dumps(log_data, indent=2))
