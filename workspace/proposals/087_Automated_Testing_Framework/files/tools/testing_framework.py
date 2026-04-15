import pytest
from typing import Dict, Any

class AgentTestFramework:
    def __init__(self):
        self.test_cases = []

    def add_test_case(self, input_data: Dict[str, Any], expected_output: Dict[str, Any], description: str):
        self.test_cases.append({
            'input': input_data,
            'expected': expected_output,
            'desc': description
        })

    def run_tests(self, agent_function):
        results = []
        for case in self.test_cases:
            actual = agent_function(case['input'])
            results.append({
                'passed': actual == case['expected'],
                'case': case['desc']
            })
        return results