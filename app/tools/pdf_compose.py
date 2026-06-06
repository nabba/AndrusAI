"""
pdf_compose.py — sandboxed PDF generation for the coding/writing crews.

Exposes a single CrewAI BaseTool, ``pdf_compose``, that runs a
user-provided Python snippet in a sandbox dict pre-loaded with
matplotlib (Agg backend), reportlab, pandas, csv, and numpy. The
script writes its output to ``/app/workspace/output/`` (enforced)
and returns the absolute path the tool then surfaces back to the
agent.

Why this exists
---------------
Pre-2026-05-03 the coding crew's behaviour when asked to "produce a
PDF report" was to write Python source code as TEXT in the response
("here's a script you can run") — never actually executing it. The
problem was that no agent-visible tool advertised PDF capability;
matplotlib + reportlab were installed in the image but only the
gateway process (not the sandboxed crew) used them. This tool
closes the gap.

Design mirrors gee_run_script:

  1. User script runs in-process inside the gateway (same trust
     model as base_crew.run_python; forge gates apply when invoked
     from forged code).
  2. Pre-loaded namespace covers the 95% case (mpl, reportlab,
     pandas, csv, numpy) so short snippets stay short.
  3. Output path is rewritten to live under workspace/output/
     regardless of what the script picks — agents can't write
     PDFs anywhere else.
  4. Sandbox returns the absolute path of the produced PDF (or
     CSV / PNG companion files) so the agent can hand them to
     ``signal_send_attachment`` next.

Safety properties
-----------------
* File writes are clamped to ``/app/workspace/output/<basename>``
  via ``_safe_output_path``. Path-traversal attempts (``../``,
  absolute paths) get stripped and the file lands in the workspace
  output directory anyway.
* No outbound network. matplotlib uses the Agg backend; no display
  / X11 / browser surface.
* Heavy figures stay in memory until ``pdf.savefig()`` flushes.
  Per-call wall-clock cap defaults to 60s (PDF rendering is local
  CPU-bound; 60s is plenty for any reasonable report).
* Stdout / stderr are captured and returned to the caller — no
  silent leaks, no corrupting the parent process's logs.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import warnings as _warnings
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Workspace-rooted output directory. ALL agent-produced reports
# land here; the dir is volume-mounted into the host so users can
# pick the files up + signal_send_attachment finds them at a
# predictable location.
_OUTPUT_DIR = Path("/app/workspace/output")


# ─── Heavy-import cache (matplotlib + reportlab) ────────────────────
#
# We import matplotlib/reportlab at MODULE LOAD time and cache the
# refs, so `_build_sandbox()` is a fast dict-construction. This also
# works around a real production bug: pydantic monkey-patches
# `warnings.warn` with a filtered wrapper that does NOT accept
# `skip_file_prefixes` (Python 3.12+ kwarg used by matplotlib during
# import). If we import matplotlib lazily AFTER pydantic loads, the
# import bombs with:
#   TypeError: filtered_warn() got an unexpected keyword argument
#   'skip_file_prefixes'
# We side-step that by swapping in a no-op warn shim around the
# heavy imports, then restoring whatever was there before.

def _import_with_warn_shim():
    """Import matplotlib + reportlab with a warn-shim active.

    Returns ``(mpl_pack, rl_pack)`` where each is a dict of cached
    module/symbol references, or {} if the optional pack isn't
    available.

    The shim is removed before returning so the rest of the program
    sees normal warning behaviour.
    """

    def _swallow(*_a, **_kw):  # noqa: D401 — eats every kwarg, incl. skip_file_prefixes
        return None

    _orig_warn = _warnings.warn
    _warnings.warn = _swallow  # type: ignore[assignment]
    try:
        # ── matplotlib ──
        mpl_pack: dict[str, Any] = {}
        try:
            import matplotlib as _mpl
            _mpl.use("Agg")  # CRITICAL — no X11 / display in container
            import matplotlib.pyplot as _plt
            from matplotlib.backends.backend_pdf import PdfPages as _PdfPages
            mpl_pack = {"matplotlib": _mpl, "plt": _plt, "PdfPages": _PdfPages}
        except Exception as exc:  # noqa: BLE001
            logger.warning("pdf_compose: matplotlib import failed: %s", exc)

        # ── reportlab (optional) ──
        rl_pack: dict[str, Any] = {}
        try:
            from reportlab.lib import colors as _rl_colors
            from reportlab.lib.pagesizes import letter as _letter, A4 as _A4
            from reportlab.lib.styles import getSampleStyleSheet as _getSampleStyleSheet
            from reportlab.platypus import (
                SimpleDocTemplate as _SimpleDocTemplate,
                Paragraph as _Paragraph,
                Spacer as _Spacer,
                Table as _Table,
                TableStyle as _TableStyle,
                Image as _RLImage,
                PageBreak as _PageBreak,
            )
            from reportlab.pdfgen import canvas as _rl_canvas
            rl_pack = {
                "colors": _rl_colors,
                "letter": _letter,
                "A4": _A4,
                "getSampleStyleSheet": _getSampleStyleSheet,
                "SimpleDocTemplate": _SimpleDocTemplate,
                "Paragraph": _Paragraph,
                "Spacer": _Spacer,
                "Table": _Table,
                "TableStyle": _TableStyle,
                "Image": _RLImage,
                "PageBreak": _PageBreak,
                "canvas": _rl_canvas,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("pdf_compose: reportlab import failed: %s", exc)

        return mpl_pack, rl_pack
    finally:
        _warnings.warn = _orig_warn  # type: ignore[assignment]


# numpy / pandas don't trip the pydantic warn-shim issue, but we
# cache them here for the same fast-sandbox-construction reason.
_NP = None
_PD = None
try:
    import numpy as _NP  # type: ignore  # noqa
except Exception:
    pass
try:
    import pandas as _PD  # type: ignore  # noqa
except Exception:
    pass

_MPL_PACK, _RL_PACK = _import_with_warn_shim()


def _safe_output_path(user_path: str | os.PathLike) -> Path:
    """Map any caller-supplied path into ``/app/workspace/output/``.

    Path-traversal attempts (``..``, absolute paths) get reduced to
    the basename. Empty / falsy → returns the output dir itself.
    """
    p = Path(str(user_path or "")).expanduser()
    base = p.name or "output.pdf"
    # Strip any path components — agents can't write to /etc/, etc.
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if not safe:
        safe = "output.pdf"
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return _OUTPUT_DIR / safe


def _build_sandbox() -> dict[str, Any]:
    """Pre-populate a sandbox dict with the libraries needed for 95%
    of report-generation scripts. Heavy clients (geopandas, fpdf,
    weasyprint) can still be imported by the script itself.

    All heavy imports (matplotlib, reportlab) happen at MODULE load
    time behind a warn-shim — see ``_import_with_warn_shim``. This
    function just hands references out of the module-level cache.
    """
    import csv as csv_mod
    import json as json_mod

    return {
        # matplotlib (None if import failed — agent must check)
        "plt": _MPL_PACK.get("plt"),
        "PdfPages": _MPL_PACK.get("PdfPages"),
        "matplotlib": _MPL_PACK.get("matplotlib"),
        # numerical
        "np": _NP,
        "pd": _PD,
        # stdlib
        "csv": csv_mod,
        "json": json_mod,
        # reportlab (None / empty dict if import failed — agent must check)
        "reportlab": _RL_PACK or None,
        # the safe path helper — agent calls safe_output_path("foo.pdf")
        # to get a clamped path under workspace/output/
        "safe_output_path": _safe_output_path,
        # The output-dir constant the agent should respect
        "OUTPUT_DIR": str(_OUTPUT_DIR),
        # Variable the script assigns its primary output path to
        "result": None,
    }


def _run_user_script(script: str, timeout_s: int = 60) -> dict[str, Any]:
    """Execute the user's PDF-generation script.

    Returns ``{ok, stdout, stderr, files, result, error}``:
      * ``ok``: True if the script ran without uncaught exception.
      * ``stdout`` / ``stderr``: captured during the run.
      * ``files``: list of absolute paths under ``/app/workspace/output/``
        that were created or modified during the script run (delta vs
        the directory's pre-script state).
      * ``result``: value of the script's ``result`` variable, useful
        when the agent wants to thread a single primary path back.
      * ``error``: short exception message when ``ok=False``.
    """
    sandbox = _build_sandbox()
    # Restrict __builtins__ before running agent-authored code in-process —
    # blocks egress/process-spawn imports + os.system/open('/proc/...') etc.
    # (whole-project review 2026-06-04). See app/tools/_safe_exec.py.
    from app.tools._safe_exec import harden
    harden(sandbox)
    stdout = StringIO()
    stderr = StringIO()

    # Snapshot the output dir so we can compute the delta after the run.
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pre = {p.resolve() for p in _OUTPUT_DIR.iterdir()} if _OUTPUT_DIR.exists() else set()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(script, sandbox)
    except Exception as exc:
        return {
            "ok": False,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "files": [],
            "result": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    post = {p.resolve() for p in _OUTPUT_DIR.iterdir()} if _OUTPUT_DIR.exists() else set()
    new_or_modified = sorted(p for p in post if p not in pre)

    raw_result = sandbox.get("result")
    return {
        "ok": True,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "files": [str(p) for p in new_or_modified],
        "result": str(raw_result) if raw_result else None,
        "error": None,
    }


# ── Sandboxed (out-of-process) execution — #5, 2026-06-06 ────────────────────
# When ``sandboxed_tool_exec_enabled`` is ON, the LLM-authored script runs in an
# ephemeral evolver CONTAINER instead of the gateway process. The container is
# the real isolation boundary (the in-process ``_safe_exec`` hardening is only
# defence-in-depth — its own docstring concedes a crafted payload can escape).
# A container, unlike a host subprocess, does NOT share the gateway's chromadb
# files, so it cannot reintroduce the §77/§55 dual-writer corruption. Artifacts
# come back base64 over the stdout sentinel (``VOLUMES: 0`` → no shared fs) and
# are re-written here to the real output dir. Default OFF; on ANY infrastructure
# failure (image not built / transport) this returns ``None`` so the caller
# falls back to the in-process path and the tool keeps working.

_SANDBOX_ENTRYPOINT = ["python3", "-m", "app.tools.pdf_compose_job"]
_SANDBOX_MEMORY_BYTES = 2 * 1024**3


def _run_user_script_sandboxed(
    script: str,
    *,
    timeout_s: int = 60,
    run_job=None,
    image: str | None = None,
) -> dict[str, Any] | None:
    """Run the PDF-compose script in an ephemeral container.

    Returns a dict shaped like ``_run_user_script`` (ok/stdout/stderr/files/
    result/error) on success — ``files`` are paths written under the REAL
    output dir from the container's base64 artifacts.

    Returns ``None`` on INFRASTRUCTURE failure ONLY (evolver image absent,
    spawn/transport/parse error) so the caller can fall back to in-process. A
    script that merely errored still ran in the container and returns a normal
    ``ok=False`` dict — never ``None`` — so a malicious script cannot force the
    insecure in-process fallback.
    """
    _log = logging.getLogger(__name__)
    try:
        from app.self_improvement import evolver_spawn
        from app.tools import pdf_compose_job
    except Exception as exc:  # pragma: no cover - import guard
        _log.warning("pdf_compose sandbox: import failed (%s) — falling back", exc)
        return None

    img = image or os.environ.get("EVOLVER_IMAGE", "botarmy-evolver:latest")
    runner = run_job or evolver_spawn.run_container_job

    # Gate on image presence only for the REAL runner (tests inject run_job).
    if run_job is None:
        try:
            if not evolver_spawn.image_exists(img):
                _log.warning(
                    "pdf_compose sandbox: evolver image %s not built — falling back to "
                    "in-process (run: docker compose --profile evolver build evolver)", img,
                )
                return None
        except Exception:
            return None

    try:
        envelope = runner(
            {"script": script, "timeout_s": int(timeout_s)},
            image=img,
            job_env_var="AAI_PDF_COMPOSE_JOB",
            entrypoint=_SANDBOX_ENTRYPOINT,
            extract_fn=pdf_compose_job.extract_result,
            memory_bytes=_SANDBOX_MEMORY_BYTES,
            network_mode="none",  # PDF rendering needs no network — enforce it
            timeout_s=int(timeout_s) + 30,  # container/spawn overhead beyond the script budget
        )
    except Exception as exc:  # run_container_job shouldn't raise; belt-and-suspenders
        _log.warning("pdf_compose sandbox: spawn raised (%s) — falling back", exc)
        return None

    if not isinstance(envelope, dict) or not envelope.get("ok"):
        _log.warning(
            "pdf_compose sandbox: no container result (%s) — falling back",
            (envelope or {}).get("error") if isinstance(envelope, dict) else envelope,
        )
        return None

    inner = envelope.get("result") or {}
    written: list[str] = []
    for art in inner.get("artifacts") or []:
        try:
            data = base64.b64decode(art["b64"])
            dest = _safe_output_path(art.get("name") or "output.pdf")
            dest.write_bytes(data)
            written.append(str(dest))
        except Exception as exc:
            _log.warning("pdf_compose sandbox: failed to write artifact %s: %s",
                         art.get("name"), exc)
    stderr = inner.get("stderr") or ""
    rejected = inner.get("rejected_artifacts") or []
    if rejected:
        notes = "; ".join(f"{r.get('name')} ({r.get('reason')})" for r in rejected)
        stderr = (stderr + f"\n[sandbox dropped {len(rejected)} oversize artifact(s): {notes}]").strip()
    return {
        "ok": bool(inner.get("ok")),
        "stdout": inner.get("stdout") or "",
        "stderr": stderr,
        "files": written,
        "result": inner.get("result"),
        "error": inner.get("error"),
    }


# ── Public factory ──────────────────────────────────────────────────

def create_pdf_tools(agent_id: str = "coder") -> list:
    """Build the PDF-composition tool list for an agent.

    Always returns ``[pdf_compose]`` — the dependencies (matplotlib,
    optional reportlab/pandas/numpy) live in the image. If a future
    deployment strips those, the tool's invocation will fail with a
    clear ImportError; the factory itself doesn't gate.
    """
    try:
        from crewai.tools import BaseTool
        from pydantic import BaseModel, Field
        from typing import Type
    except ImportError:
        return []

    class _PdfComposeInput(BaseModel):
        script: str = Field(
            description=(
                "Python snippet that builds and writes a PDF (and "
                "optionally companion CSVs/PNGs). Pre-loaded names: "
                "`plt` (matplotlib.pyplot, Agg backend), `PdfPages`, "
                "`np` (numpy, may be None), `pd` (pandas, may be "
                "None), `csv`, `json`, `reportlab` (dict of common "
                "platypus + canvas symbols, may be None), "
                "`safe_output_path(name)` → returns the absolute path "
                "under /app/workspace/output/ — ALWAYS use this; "
                "writing anywhere else gets clamped silently. Assign "
                "the primary output path to `result` so the caller "
                "can thread it forward (e.g. to signal_send_attachment)."
            ),
        )
        timeout_s: int = Field(
            default=60,
            description=(
                "Wall-clock seconds for the local PDF render. "
                "60 is plenty for any reasonable report (10 pages "
                "with charts ~ 5-15s)."
            ),
        )

    class PdfComposeTool(BaseTool):
        name: str = "pdf_compose"
        description: str = (
            "Render a PDF report locally (matplotlib + reportlab) "
            "from data you've already collected with other tools. "
            "USE THIS instead of writing Python source as the response "
            "text — the script you provide RUNS HERE and produces a "
            "real .pdf file the user can open. Pair with "
            "`signal_send_attachment` to deliver the PDF over Signal.\n\n"
            "Pattern: gather data with gee_run_script / web_search / "
            "etc., parse it into Python literals, then pass a snippet "
            "here that uses matplotlib for charts + reportlab for "
            "structured layout. Output paths MUST go through "
            "`safe_output_path('name.pdf')` to land under "
            "/app/workspace/output/.\n\n"
            "# GOOD — quick chart-only PDF via PdfPages:\n"
            "out = safe_output_path('estonia_loss.pdf')\n"
            "with PdfPages(out) as pdf:\n"
            "    fig, ax = plt.subplots()\n"
            "    ax.bar(years, values)\n"
            "    pdf.savefig(fig)\n"
            "    plt.close()\n"
            "result = str(out)\n\n"
            "# GOOD — structured layout via reportlab Platypus:\n"
            "out = safe_output_path('report.pdf')\n"
            "doc = reportlab['SimpleDocTemplate'](str(out))\n"
            "styles = reportlab['getSampleStyleSheet']()\n"
            "doc.build([reportlab['Paragraph']('Hello', styles['Title'])])\n"
            "result = str(out)\n\n"
            "Returns: list of files produced + their paths so the "
            "agent can pass them to signal_send_attachment."
        )
        args_schema: Type[BaseModel] = _PdfComposeInput

        def _run(self, script: str, timeout_s: int = 60) -> str:
            out = None
            try:
                from app.runtime_settings import get_sandboxed_tool_exec_enabled

                if get_sandboxed_tool_exec_enabled():
                    # Isolated container path (default OFF). Returns None on infra
                    # failure (image not built / transport) → fall back in-process.
                    out = _run_user_script_sandboxed(script, timeout_s=timeout_s)
            except Exception:
                out = None
            if out is None:
                out = _run_user_script(script, timeout_s=timeout_s)
            if not out["ok"]:
                return (
                    f"PDF compose failed: {out['error']}\n\n"
                    f"--- stdout ---\n{out['stdout']}\n"
                    f"--- stderr ---\n{out['stderr']}"
                )
            lines = ["PDF compose completed."]
            if out["files"]:
                lines.append(f"\n--- files produced ({len(out['files'])}) ---")
                for f in out["files"]:
                    try:
                        size = os.path.getsize(f)
                        lines.append(f"  {f}  ({size:,} bytes)")
                    except OSError:
                        lines.append(f"  {f}  (size unknown)")
            else:
                lines.append("\nWARNING: no new files in /app/workspace/output/ — did the script forget to call safe_output_path?")
            if out["result"]:
                lines.append(f"\n--- result ---\n{out['result']}")
            if out["stdout"]:
                lines.append(f"\n--- stdout ---\n{out['stdout'].rstrip()}")
            return "\n".join(lines)

    return [PdfComposeTool()]


# ── Tool registry annotation ────────────────────────────────────────
# Phase 1a passive registration. The legacy `create_pdf_tools()` factory
# above keeps working unchanged; annotating below also lets the
# registry know this tool exists for tool_search / LoadableAgent.
try:
    from app.tool_registry import Lifecycle, Tier, register_tool

    _PDF_COMPOSE_DESCRIPTION = (
        "Render a PDF report locally (matplotlib + reportlab) from "
        "data you've already collected with other tools. USE THIS "
        "instead of writing Python source as the response text — the "
        "script you provide RUNS HERE and produces a real .pdf file "
        "the user can open. Pair with `signal_send_attachment` to "
        "deliver the PDF over Signal.\n\n"
        "Pre-loaded names: `plt`, `PdfPages`, `np`, `pd`, `csv`, "
        "`json`, `reportlab`, `safe_output_path()`. See the tool's "
        "args_schema for the full pattern."
    )

    @register_tool(
        name="pdf_compose",
        capabilities=["renders-pdf", "renders-chart"],
        description=_PDF_COMPOSE_DESCRIPTION,
        tier=Tier.PRODUCTION,
        lifecycle=Lifecycle.SINGLETON,
        # Tool always reachable — matplotlib/reportlab live in the image.
        guard=lambda: True,
    )
    def _pdf_compose_registry_factory(agent_id: str = "coder"):
        return create_pdf_tools(agent_id=agent_id)[0]
except ImportError:
    # tool_registry not yet imported during a partial-build; harmless.
    pass
