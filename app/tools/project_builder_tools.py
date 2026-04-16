"""
project_builder_tools.py — Project scaffolding, build, and packaging tools.

All operations execute on the host via bridge.
Templates are embedded as Python dicts — no external template files.

Usage:
    from app.tools.project_builder_tools import create_project_builder_tools
    tools = create_project_builder_tools("devops")
"""

import json
import logging

logger = logging.getLogger(__name__)

# ── Project templates ─────────────────────────────────────────────

_TEMPLATES = {
    "python-cli": {
        "description": "Python CLI application with pyproject.toml",
        "files": {
            "pyproject.toml": '''\
[project]
name = "{name}"
version = "0.1.0"
description = ""
requires-python = ">=3.11"
dependencies = []

[project.scripts]
{name} = "{name}.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
''',
            "src/{name}/__init__.py": "",
            "src/{name}/main.py": '''\
def main() -> None:
    print("Hello from {name}!")

if __name__ == "__main__":
    main()
''',
            "tests/__init__.py": "",
            "tests/test_main.py": '''\
from {name}.main import main

def test_main(capsys):
    main()
    captured = capsys.readouterr()
    assert "{name}" in captured.out.lower() or "hello" in captured.out.lower()
''',
            "README.md": "# {name}\n\nA Python CLI application.\n",
            ".gitignore": "__pycache__/\n*.pyc\ndist/\n*.egg-info/\n.venv/\n",
        },
    },
    "python-api": {
        "description": "FastAPI web application with Docker",
        "files": {
            "pyproject.toml": '''\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.100.0", "uvicorn[standard]>=0.20.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
''',
            "src/{name}/__init__.py": "",
            "src/{name}/app.py": '''\
from fastapi import FastAPI

app = FastAPI(title="{name}")

@app.get("/")
async def root():
    return {{"message": "Hello from {name}!"}}

@app.get("/health")
async def health():
    return {{"status": "ok"}}
''',
            "Dockerfile": '''\
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "{name}.app:app", "--host", "0.0.0.0", "--port", "8000"]
''',
            "tests/__init__.py": "",
            "tests/test_app.py": '''\
from fastapi.testclient import TestClient
from {name}.app import app

client = TestClient(app)

def test_root():
    response = client.get("/")
    assert response.status_code == 200

def test_health():
    response = client.get("/health")
    assert response.json()["status"] == "ok"
''',
            "README.md": "# {name}\n\nA FastAPI web application.\n",
            ".gitignore": "__pycache__/\n*.pyc\ndist/\n*.egg-info/\n.venv/\n",
        },
    },
    "node-api": {
        "description": "Node.js API with Express",
        "files": {
            "package.json": '''\
{{
  "name": "{name}",
  "version": "0.1.0",
  "type": "module",
  "scripts": {{
    "start": "node src/index.js",
    "dev": "node --watch src/index.js",
    "test": "node --test tests/"
  }},
  "dependencies": {{
    "express": "^4.18.0"
  }}
}}
''',
            "src/index.js": '''\
import express from 'express';

const app = express();
const PORT = process.env.PORT || 3000;

app.get('/', (req, res) => {{
  res.json({{ message: 'Hello from {name}!' }});
}});

app.get('/health', (req, res) => {{
  res.json({{ status: 'ok' }});
}});

app.listen(PORT, () => {{
  console.log(`{name} running on port ${{PORT}}`);
}});

export default app;
''',
            "tests/test_app.js": '''\
import {{ describe, it }} from 'node:test';
import assert from 'node:assert';

describe('{name}', () => {{
  it('should be importable', async () => {{
    const mod = await import('../src/index.js');
    assert.ok(mod);
  }});
}});
''',
            "README.md": "# {name}\n\nA Node.js API application.\n",
            ".gitignore": "node_modules/\ndist/\n.env\n",
        },
    },
    "static-site": {
        "description": "Static HTML/CSS/JS website",
        "files": {
            "index.html": '''\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{name}</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <main>
    <h1>{name}</h1>
    <p>Welcome to {name}.</p>
  </main>
  <script src="app.js"></script>
</body>
</html>
''',
            "style.css": '''\
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 0 auto; padding: 2rem; }}
h1 {{ margin-bottom: 1rem; }}
''',
            "app.js": "// {name} application\nconsole.log('{name} loaded');\n",
            "README.md": "# {name}\n\nA static website.\n",
        },
    },
}


def create_project_builder_tools(agent_id: str) -> list:
    """Create project scaffolding and build tools via bridge."""
    try:
        from app.bridge_client import get_bridge
        bridge = get_bridge(agent_id)
        if not bridge:
            return []
        if not bridge.is_available():
            return []
    except Exception:
        return []

    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    # ── Tool definitions ──────────────────────────────────────────

    class _ScaffoldInput(BaseModel):
        name: str = Field(description="Project name (lowercase, no spaces)")
        template: str = Field(
            description="Template: python-cli, python-api, node-api, static-site"
        )
        base_path: str = Field(
            default="/tmp/crewai-projects",
            description="Base directory to create project in",
        )

    class ScaffoldProjectTool(BaseTool):
        name: str = "scaffold_project"
        description: str = (
            "Create a new project from a template. Available templates: "
            "python-cli, python-api, node-api, static-site. "
            "Creates complete project structure with code, tests, and config."
        )
        args_schema: Type[BaseModel] = _ScaffoldInput

        def _run(self, name: str, template: str, base_path: str = "/tmp/crewai-projects") -> str:
            if template not in _TEMPLATES:
                available = ", ".join(_TEMPLATES.keys())
                return f"Unknown template: {template}. Available: {available}"

            name = name.lower().replace(" ", "-").replace("_", "-")
            project_dir = f"{base_path}/{name}"

            # Create project directory
            bridge.execute(["mkdir", "-p", project_dir])

            tmpl = _TEMPLATES[template]
            created = []
            for filepath, content in tmpl["files"].items():
                # Replace template variables
                filepath = filepath.replace("{name}", name.replace("-", "_"))
                content = content.replace("{name}", name.replace("-", "_"))

                full_path = f"{project_dir}/{filepath}"
                # Create parent directories
                parent = "/".join(full_path.split("/")[:-1])
                bridge.execute(["mkdir", "-p", parent])

                result = bridge.write_file(full_path, content, create_dirs=True)
                if "error" not in result:
                    created.append(filepath)

            # Initialize git
            bridge.execute(["git", "init", project_dir])

            return (
                f"Project '{name}' created at {project_dir}\n"
                f"Template: {template} ({tmpl['description']})\n"
                f"Files: {len(created)}\n"
                f"Next: install dependencies, then build/test."
            )

    class _BuildInput(BaseModel):
        project_path: str = Field(description="Path to the project directory")
        action: str = Field(
            default="install",
            description="Action: install (deps), build, test, package",
        )

    class BuildProjectTool(BaseTool):
        name: str = "build_project"
        description: str = (
            "Build operations on a project: install dependencies, run build, "
            "execute tests, or create a distributable package."
        )
        args_schema: Type[BaseModel] = _BuildInput

        def _run(self, project_path: str, action: str = "install") -> str:
            # Detect project type
            has_pyproject = "error" not in bridge.execute(["test", "-f", f"{project_path}/pyproject.toml"])
            has_package_json = "error" not in bridge.execute(["test", "-f", f"{project_path}/package.json"])

            if action == "install":
                if has_pyproject:
                    result = bridge.execute(["sh", "-c", f"cd {project_path} && pip install -e '.[dev]' 2>&1 || pip install -e . 2>&1"])
                elif has_package_json:
                    result = bridge.execute(["sh", "-c", f"cd {project_path} && npm install 2>&1"])
                else:
                    return "Cannot detect project type (no pyproject.toml or package.json)."
            elif action == "build":
                if has_pyproject:
                    result = bridge.execute(["sh", "-c", f"cd {project_path} && python -m build 2>&1"])
                elif has_package_json:
                    result = bridge.execute(["sh", "-c", f"cd {project_path} && npm run build 2>&1"])
                else:
                    return "Cannot detect project type."
            elif action == "test":
                if has_pyproject:
                    result = bridge.execute(["sh", "-c", f"cd {project_path} && python -m pytest tests/ -v 2>&1"])
                elif has_package_json:
                    result = bridge.execute(["sh", "-c", f"cd {project_path} && npm test 2>&1"])
                else:
                    return "Cannot detect project type."
            elif action == "package":
                if has_pyproject:
                    result = bridge.execute(["sh", "-c", f"cd {project_path} && python -m build 2>&1"])
                elif has_package_json:
                    result = bridge.execute(["sh", "-c", f"cd {project_path} && npm pack 2>&1"])
                else:
                    name = project_path.split("/")[-1]
                    result = bridge.execute(["sh", "-c", f"cd {project_path}/.. && tar -czf {name}.tar.gz {name}/ 2>&1"])
            else:
                return f"Unknown action: {action}. Use: install, build, test, package."

            if "error" in result:
                return f"Error: {result.get('detail', result['error'])}"
            output = result.get("stdout", "")
            stderr = result.get("stderr", "")
            return (output + ("\n\nSTDERR:\n" + stderr if stderr else ""))[:3000]

    return [
        ScaffoldProjectTool(),
        BuildProjectTool(),
    ]
