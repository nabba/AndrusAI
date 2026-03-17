from crewai.tools import tool
import docker
import tempfile
import pathlib
from app.config import get_settings

settings = get_settings()

WORKSPACE = pathlib.Path("/app/workspace/output").resolve()


@tool("execute_code")
def execute_code(language: str, code: str) -> str:
    """
    Execute code safely inside a Docker sandbox.
    language: 'python', 'bash', 'node', or 'ruby'
    code: the source code to execute
    Returns stdout + stderr, max 4000 chars.
    """
    ext_map = {"python": ".py", "bash": ".sh", "node": ".js", "ruby": ".rb"}
    cmd_map = {"python": "python3", "bash": "bash", "node": "node", "ruby": "ruby"}

    ext = ext_map.get(language, ".py")
    cmd = cmd_map.get(language, "python3")

    # Ensure workspace exists
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    # Write code to a temp file in workspace (readable by sandbox)
    with tempfile.NamedTemporaryFile(
        suffix=ext, dir=WORKSPACE, delete=False, mode="w"
    ) as f:
        f.write(code)
        host_path = pathlib.Path(f.name)

    container_path = f"/sandbox/{host_path.name}"
    client = docker.from_env()

    try:
        result = client.containers.run(
            settings.sandbox_image,
            command=f"{cmd} {container_path}",
            volumes={str(WORKSPACE): {"bind": "/sandbox", "mode": "ro"}},
            network_disabled=True,  # No network in sandbox
            read_only=True,  # No writing to container FS
            mem_limit=settings.sandbox_memory_limit,
            nano_cpus=int(settings.sandbox_cpu_limit * 1e9),
            cap_drop=["ALL"],  # Drop all Linux capabilities
            security_opt=["no-new-privileges:true"],
            remove=True,  # Auto-remove after run
            timeout=settings.sandbox_timeout_seconds,
            stdout=True,
            stderr=True,
        )
        output = result.decode("utf-8", errors="replace")
    except docker.errors.ContainerError as e:
        output = f"Runtime error:\n{e.stderr.decode()}"
    except Exception as e:
        output = f"Execution error: {str(e)}"
    finally:
        try:
            host_path.unlink()
        except Exception:
            pass

    return output[:4000]
