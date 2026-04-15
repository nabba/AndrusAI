import unittest
import subprocess
from io import StringIO
import sys

class CodeValidator:
    def __init__(self):
        self.test_cases = []
    
    def add_test_case(self, code, expected_output):
        self.test_cases.append((code, expected_output))
    
    def run_tests(self):
        results = []
        for code, expected in self.test_cases:
            try:
                # Capture stdout
                old_stdout = sys.stdout
                sys.stdout = mystdout = StringIO()
                
                # Execute code
                exec(code)
                
                # Get output
                sys.stdout = old_stdout
                actual = mystdout.getvalue().strip()
                
                results.append({
                    'code': code,
                    'expected': expected,
                    'actual': actual,
                    'passed': actual == expected
                })
            except Exception as e:
                results.append({
                    'code': code,
                    'error': str(e),
                    'passed': False
                })
        return results