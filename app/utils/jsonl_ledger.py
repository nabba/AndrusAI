"""Unified JSONL append-only ledger primitive (Phase E.1, 2026-05-22).

Roughly 15 modules under ``app/`` ship the same shape of code:

  * an append-only JSONL file at a per-subsystem path
  * a thread-safe ``append(record)`` that opens-for-append, writes
    one compact JSON line, closes the file
  * an ``iter_all`` / ``load_all`` reader that tolerates malformed
    rows (skipped with a debug log)
  * a ``stats()`` helper returning ``{rows, bytes, last_ts}``
  * a ``reset_for_tests(base_dir)`` test-isolation knob

This module collapses that pattern into one class. Subsystems opt in
by instantiating a :class:`JsonlLedger` with a path resolver and the
dataclass type they persist. Existing modules don't need to migrate
— this is additive infrastructure for new subsystems and for one-off
cleanup of the chronically copy-pasted boilerplate.

Composition
───────────

  * Bounded retention: pass ``max_rows`` (or compose with
    :mod:`app.utils.jsonl_retention`).
  * Custom serialisation: subclass and override ``serialise_one`` /
    ``rehydrate_one``.
  * Per-test isolation: ``reset_for_tests(base_dir)`` overrides the
    base directory; the resolver re-runs on every call so tests get
    a fresh path each time.

Design constraints
──────────────────

  * **Failure-isolated reads** — a single corrupted row never breaks
    the whole iteration. ``debug``-level log + skip.
  * **Thread-safe writes** — module-level lock makes the append a
    single fsync per row. Cheap at v1 row volumes; if a subsystem
    ever needs bursty writes, a batched ``append_many`` could be
    added.
  * **No vendor-lock** — pure-stdlib + no external dependencies.
  * **Reset for tests is global per-instance** — every test that
    uses the ledger calls ``reset_for_tests(tmp_path)`` to point at
    a tmp dir, and ``reset_for_tests(None)`` in teardown.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import (
    Any,
    Callable,
    Generic,
    Iterator,
    Optional,
    TypeVar,
)

logger = logging.getLogger(__name__)


T = TypeVar("T")


class JsonlLedger(Generic[T]):
    """One append-only JSONL file with a small uniform API.

    Parameters
    ----------
    name
        Human-readable identifier used in log messages — e.g.
        ``"benchmarks_runs"`` or ``"executor_runs"``.
    default_path
        Function returning the path the ledger lives at when no
        override is set. Called every time the path is needed so
        late-bound base directories (env vars, runtime_settings) are
        honoured.
    rehydrate
        Function ``(dict) -> T`` rebuilding one record from its
        on-disk dict form. The natural fit is the dataclass's
        ``from_dict`` classmethod (or ``cls(**row)`` for simple
        shapes). Records that fail rehydration are skipped, not
        raised.
    serialise
        Optional override for record → dict. Defaults to
        ``dataclasses.asdict`` when ``T`` is a dataclass, else
        ``vars(record)``.
    ts_field
        Field name whose value is recorded as ``last_ts`` in
        :meth:`stats`. Defaults to ``"ts"`` — most ledgers use that
        name. Pass another string for ledgers that use e.g.
        ``"created_at"``.
    """

    def __init__(
        self,
        *,
        name: str,
        default_path: Callable[[], Path],
        rehydrate: Callable[[dict[str, Any]], T],
        serialise: Optional[Callable[[T], dict[str, Any]]] = None,
        ts_field: str = "ts",
    ) -> None:
        self._name = name
        self._default_path = default_path
        self._rehydrate = rehydrate
        self._serialise_override = serialise
        self._ts_field = ts_field
        self._path_override: Optional[Path] = None
        self._lock = threading.RLock()

    # ── Path resolution ──────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._name

    def path(self) -> Path:
        """Return the active ledger path. Honours
        :meth:`reset_for_tests` override."""
        if self._path_override is not None:
            return self._path_override
        return self._default_path()

    # ── Serialisation ────────────────────────────────────────────

    def serialise_one(self, record: T) -> dict[str, Any]:
        """Record → dict. Override or pass ``serialise=`` to customise."""
        if self._serialise_override is not None:
            return self._serialise_override(record)
        if is_dataclass(record):
            return asdict(record)
        return dict(vars(record))

    # ── Writer ───────────────────────────────────────────────────

    def append(self, record: T) -> None:
        """Append one record to the file. Thread-safe.

        Raises only on hard I/O errors (disk full, perm denied);
        callers may catch + degrade or let the error propagate.
        """
        target = self.path()
        with self._lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                payload = self.serialise_one(record)
            except Exception as exc:
                logger.warning(
                    "jsonl_ledger[%s]: serialise failed (%s) — skipping",
                    self._name, exc,
                )
                return
            line = json.dumps(payload, separators=(",", ":"), default=str)
            with target.open("a", encoding="utf-8") as fp:
                fp.write(line)
                fp.write("\n")

    # ── Readers ──────────────────────────────────────────────────

    def iter_all(self) -> Iterator[T]:
        """Yield every persisted record, in append order.

        Malformed lines (bad JSON, wrong shape, rehydrate raised)
        are skipped with a debug log. Missing file returns no rows.
        """
        target = self.path()
        if not target.exists():
            return
        try:
            with target.open("r", encoding="utf-8") as fp:
                for lineno, raw in enumerate(fp, start=1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        d = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.debug(
                            "jsonl_ledger[%s]: skipping malformed "
                            "line %d", self._name, lineno,
                        )
                        continue
                    if not isinstance(d, dict):
                        continue
                    try:
                        yield self._rehydrate(d)
                    except (KeyError, TypeError, ValueError):
                        logger.debug(
                            "jsonl_ledger[%s]: skipping unrehydratable "
                            "line %d", self._name, lineno,
                        )
                        continue
        except OSError as exc:
            logger.warning(
                "jsonl_ledger[%s]: read failed: %s", self._name, exc,
            )
            return

    def load_all(self) -> list[T]:
        """Materialise every record into a list."""
        return list(self.iter_all())

    # ── Stats ────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Quick summary for operator surfaces.

        Returns ``{rows, bytes, last_ts}``. Cheap — single stat() +
        a single-pass line count. ``last_ts`` is the value of the
        ``ts_field`` from the last well-formed row (empty string when
        unavailable).
        """
        target = self.path()
        if not target.exists():
            return {"rows": 0, "bytes": 0, "last_ts": ""}
        try:
            size = target.stat().st_size
        except OSError:
            size = 0
        rows = 0
        last_ts = ""
        try:
            with target.open("r", encoding="utf-8") as fp:
                for line in fp:
                    line = line.strip()
                    if not line:
                        continue
                    rows += 1
                    try:
                        d = json.loads(line)
                        if isinstance(d, dict) and self._ts_field in d:
                            v = d[self._ts_field]
                            if v is not None:
                                last_ts = str(v)
                    except (json.JSONDecodeError, ValueError):
                        continue
        except OSError as exc:
            logger.warning(
                "jsonl_ledger[%s]: stats read failed: %s",
                self._name, exc,
            )
        return {"rows": rows, "bytes": size, "last_ts": last_ts}

    # ── Test helpers ─────────────────────────────────────────────

    def reset_for_tests(self, path: Optional[Path]) -> None:
        """Override the resolved path. Pass ``None`` to clear.

        ``path`` is the full file path, not a directory — caller
        controls both base + filename so each test can choose its
        own scheme.
        """
        with self._lock:
            self._path_override = path


__all__ = ["JsonlLedger"]
