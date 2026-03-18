"""
ollama_manager.py — Dynamic Ollama model lifecycle management.

Manages model pulling, loading, and unloading to optimize memory:
  - Auto-pulls models on first use (no manual setup needed)
  - Keeps only ONE large model loaded at a time (saves RAM)
  - Unloads the previous model before loading the next one
  - Tracks which model is currently "hot" in GPU/RAM
  - Thread-safe: concurrent crews wait for model swaps

Ollama API endpoints used:
  POST /api/pull    — download a model
  POST /api/generate — with keep_alive=0 to unload
  POST /api/generate — with keep_alive=-1 to keep loaded
  GET  /api/tags    — list available models
  GET  /api/ps      — list running (loaded) models
"""

import logging
import threading
import time
import requests

from app.config import get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_current_model: str | None = None  # model currently loaded in Ollama


def _base_url() -> str:
    return get_settings().local_llm_base_url.rstrip("/")


def _api(method: str, path: str, json: dict = None, timeout: int = 10) -> dict | None:
    """Make an Ollama API call. Returns parsed JSON or None on failure."""
    try:
        url = f"{_base_url()}{path}"
        if method == "GET":
            r = requests.get(url, timeout=timeout)
        else:
            r = requests.post(url, json=json or {}, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        logger.debug(f"ollama_manager: {method} {path} → {r.status_code}")
    except Exception as exc:
        logger.debug(f"ollama_manager: {method} {path} failed: {exc}")
    return None


def is_ollama_reachable() -> bool:
    """Quick check if Ollama is responding."""
    try:
        r = requests.get(f"{_base_url()}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def list_local_models() -> list[str]:
    """Return names of models already pulled to disk."""
    data = _api("GET", "/api/tags")
    if data:
        return [m.get("name", "") for m in data.get("models", [])]
    return []


def list_running_models() -> list[str]:
    """Return names of models currently loaded in memory."""
    data = _api("GET", "/api/ps")
    if data:
        return [m.get("name", "") for m in data.get("models", [])]
    return []


def is_model_available(model: str) -> bool:
    """Check if a model is already pulled (on disk)."""
    return model in list_local_models()


def pull_model(model: str) -> bool:
    """
    Pull (download) a model. This can take minutes for large models.
    Streams progress and returns True on success.
    """
    logger.info(f"ollama_manager: pulling model {model} (this may take a while)...")
    try:
        url = f"{_base_url()}/api/pull"
        # Use streaming to handle large downloads without timeout
        with requests.post(url, json={"name": model}, stream=True, timeout=600) as r:
            if r.status_code != 200:
                logger.error(f"ollama_manager: pull {model} failed: HTTP {r.status_code}")
                return False
            last_status = ""
            for line in r.iter_lines():
                if line:
                    try:
                        import json
                        data = json.loads(line)
                        status = data.get("status", "")
                        if status != last_status:
                            logger.info(f"ollama_manager: pull {model}: {status}")
                            last_status = status
                    except Exception:
                        pass
        logger.info(f"ollama_manager: pull {model} completed")
        return True
    except Exception as exc:
        logger.error(f"ollama_manager: pull {model} failed: {exc}")
        return False


def unload_model(model: str) -> None:
    """Unload a model from memory (keep_alive=0)."""
    logger.info(f"ollama_manager: unloading {model}")
    try:
        requests.post(
            f"{_base_url()}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=30,
        )
    except Exception as exc:
        logger.debug(f"ollama_manager: unload {model} failed: {exc}")


def load_model(model: str) -> None:
    """Pre-load a model into memory (keep_alive=-1 = forever until unloaded)."""
    logger.info(f"ollama_manager: loading {model} into memory")
    try:
        requests.post(
            f"{_base_url()}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": -1},
            timeout=120,
        )
    except Exception as exc:
        logger.debug(f"ollama_manager: load {model} failed: {exc}")


def ensure_model_ready(model: str) -> bool:
    """
    Thread-safe: ensure a model is pulled and loaded.
    Unloads the previous model first to free memory.
    Returns True if model is ready to use, False on failure.
    """
    global _current_model

    if not is_ollama_reachable():
        return False

    with _lock:
        # Already the active model? Nothing to do
        if _current_model == model:
            return True

        # Step 1: Pull if not on disk
        if not is_model_available(model):
            logger.info(f"ollama_manager: model {model} not found locally, pulling...")
            if not pull_model(model):
                return False

        # Step 2: Unload current model to free memory
        if _current_model and _current_model != model:
            unload_model(_current_model)
            # Brief pause for memory to release
            time.sleep(1)

        # Step 3: Load the new model
        load_model(model)
        _current_model = model
        logger.info(f"ollama_manager: {model} is now the active model")
        return True


def unload_all() -> None:
    """Unload all models from memory (e.g. during idle periods)."""
    global _current_model
    with _lock:
        for model in list_running_models():
            unload_model(model)
        _current_model = None
        logger.info("ollama_manager: all models unloaded")


def get_active_model() -> str | None:
    """Return the currently loaded model name, or None."""
    return _current_model


def model_status() -> dict:
    """Return a status summary for the 'llm' command."""
    if not is_ollama_reachable():
        return {"reachable": False}
    return {
        "reachable": True,
        "local_models": list_local_models(),
        "running_models": list_running_models(),
        "active_model": _current_model,
    }
