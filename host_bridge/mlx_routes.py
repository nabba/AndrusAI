"""
mlx_routes.py — MLX inference on host Metal GPU.

Runs ON THE HOST (M4 Max), NOT in Docker. Accessed via bridge_client.py.

Endpoints wired into the host_bridge FastAPI server:
  POST /mlx/generate  — text generation with optional LoRA adapter
  GET  /mlx/status    — availability check

NOTE: the existing host_bridge/main.py also defines /mlx/generate using a
subprocess. This module provides an in-process alternative that loads the
model once and reuses it. It is imported lazily by host_bridge/main.py so
existing deployments keep working if mlx_lm is not installed.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_model = None
_tokenizer = None
_model_name: str | None = None
_adapter_path: str | None = None


def _load(model_name: str, adapter: str = ""):
    global _model, _tokenizer, _model_name, _adapter_path
    if _model_name == model_name and _adapter_path == adapter and _model is not None:
        return _model, _tokenizer

    import mlx_lm
    t0 = time.monotonic()
    if adapter and Path(adapter).exists():
        m, tok = mlx_lm.load(model_name, adapter_path=adapter)
    else:
        m, tok = mlx_lm.load(model_name)
    _model, _tokenizer, _model_name, _adapter_path = m, tok, model_name, adapter
    logger.info(f"MLX loaded {model_name} in {time.monotonic()-t0:.1f}s")
    return m, tok


def generate(prompt: str, model_name: str = "mlx-community/Qwen2.5-7B-Instruct-4bit",
             adapter_path: str = "", max_tokens: int = 512,
             temperature: float = 0.3, seed: int = 42) -> dict:
    try:
        import mlx_lm
    except ImportError:
        return {"error": "mlx_lm not installed"}
    try:
        model, tok = _load(model_name, adapter_path)
        t0 = time.monotonic()
        if hasattr(tok, "apply_chat_template"):
            formatted = tok.apply_chat_template(
                [{"role": "user", "content": prompt}],
                tokenize=False, add_generation_prompt=True,
            )
        else:
            formatted = prompt
        response = mlx_lm.generate(model, tok, prompt=formatted,
                                    max_tokens=max_tokens, temp=temperature, seed=seed)
        return {"response": response, "tokens": len(tok.encode(response)),
                "time_s": round(time.monotonic()-t0, 2), "model": model_name,
                "adapter": adapter_path or "none"}
    except Exception as exc:
        return {"error": str(exc)[:500]}


def get_status() -> dict:
    try:
        import mlx_lm
        return {"available": True, "loaded_model": _model_name,
                "loaded_adapter": _adapter_path,
                "version": getattr(mlx_lm, "__version__", "?")}
    except ImportError:
        return {"available": False}
