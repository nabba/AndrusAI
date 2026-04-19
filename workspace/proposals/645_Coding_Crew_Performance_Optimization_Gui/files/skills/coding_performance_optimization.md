# Coding Performance Optimization Guide

## Problem
Coding tasks frequently take 3-18 minutes even for simple problems (e.g., reversing a linked list: 700-1127s, writing a decorator: 417s, running shell commands: 164-549s). This wastes resources and frustrates users.

## Root Causes
1. Over-deliberation on trivial tasks
2. Lack of code templates for common patterns
3. No task complexity triage to match effort to difficulty
4. Excessive iteration/testing cycles for simple code

## Optimization Strategies

### 1. Task Complexity Triage (Before Coding)
- **Trivial (d=1-3)**: Direct generation, no planning phase. Target: <60 seconds.
  - Examples: reverse a list, check if prime, write a decorator, run shell commands
- **Medium (d=4-6)**: Brief plan + single implementation pass. Target: <180 seconds.
  - Examples: scrape a webpage, parse a file, build a small utility
- **Complex (d=7-10)**: Full planning + iterative implementation. Target: <600 seconds.
  - Examples: refactor a codebase, build a multi-file application

### 2. Common Code Templates

#### Shell Command Execution
```python
import subprocess
result = subprocess.run(['command', 'arg'], capture_output=True, text=True, timeout=30)
print(result.stdout)
if result.returncode != 0:
    print(f'Error: {result.stderr}')
```

#### File Read/Write
```python
from pathlib import Path
content = Path('file.txt').read_text()
Path('output.txt').write_text(processed_content)
```

#### HTTP Request
```python
import requests
resp = requests.get(url, timeout=10)
data = resp.json()  # or resp.text
```

### 3. Anti-Patterns to Avoid
- Don't write unit tests for one-off scripts unless requested
- Don't add type hints, docstrings, and error handling to trivial functions unless requested
- Don't iterate multiple times on working code to 'improve' it
- Don't explain the code at length unless asked — just deliver it

### 4. Direct Execution Pattern
For shell/system tasks, compose and run ALL commands in a single execution block rather than running them one-by-one.

## Success Metrics
- Trivial tasks (d≤3): complete in <60s with 'high' quality rating
- Medium tasks (d=4-6): complete in <180s
- Reduce 'Slow' annotations by 75%
