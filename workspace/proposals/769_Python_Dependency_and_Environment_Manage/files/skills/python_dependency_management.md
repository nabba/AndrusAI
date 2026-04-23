# Python Dependency and Environment Management

## Problem

The AI agent team's `code_executor` environment may have a limited set of pre-installed Python packages. When tasks require additional libraries, the team often installs them ad-hoc, leading to version conflicts, missing dependencies, and non-reproducible results.

## Solution

Adopt a systematic approach to dependency management:

### 1. Check Available Packages

Use `pip list` or `import pkg_resources; print([d.project_name for d in pkg_resources.working_set])` to see what's already installed.

### 2. Use Virtual Environments (Optional but Recommended)

If the code_executor allows persistent filesystem, create a virtual environment:
```bash
python -m venv /path/to/venv
source /path/to/venv/bin/activate  # or activate.bat on Windows
```
Then install packages into that environment.

### 3. Maintain a `requirements.txt` File

Pin exact versions for reproducibility:
```txt
pandas==2.2.1
openpyxl==3.1.2
python-docx==1.1.0
```
Install with `pip install -r requirements.txt`.

### 4. Safe Installation Pattern

Before installing, check if package exists:
```python
import importlib.util
if importlib.util.find_spec("pandas") is None:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pandas==2.2.1"]) 
```

### 5. Document Dependencies

Each skill or code module should list required packages in its header.

## Benefits

- Consistent environments across tasks
- Easier debugging
- Avoids "works on my machine" issues
- Enables caching of dependencies

## Example Usage

When a new task requires Excel processing, add `openpyxl` to requirements and install before using.