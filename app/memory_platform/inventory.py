"""Read-only inventory and classification of legacy memory sources."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator

from app.memory_platform.models import Durability
from app.memory_platform.registry import MEMORY_SPACES


@dataclass(frozen=True, slots=True)
class SourceInventory:
    source_key: str
    backend: str
    location: str
    source_kind: str
    target_space: str
    target_durability: str
    ledger_rows: int | None = None
    live_documents: int | None = None
    bytes_on_disk: int | None = None
    warnings: tuple[str, ...] = ()


def classify_collection(collection: str) -> str | None:
    """Classify dynamic legacy collection names without querying them."""

    fixed: dict[str, str] = {}
    for key, space in MEMORY_SPACES.items():
        for location in space.legacy:
            if location.collection:
                fixed[location.collection] = key
    if collection in fixed:
        return fixed[collection]
    if collection.startswith("scope_agent_"):
        return "operational.agent_private"
    if collection.startswith("scope_research_bb--") or collection == "scope_research_bb":
        return "operational.blackboard"
    if collection.startswith("scope_project_") or collection.startswith("biz_kb_"):
        return "tenant.documents"
    return None


def iter_ledger(path: Path) -> Iterator[dict[str, object]]:
    """Yield valid JSON objects from a source ledger without mutating Chroma."""

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}") from exc
            if isinstance(row, dict):
                yield row


def ledger_counts(path: Path) -> tuple[int, dict[str, int]]:
    """Fold add/update/delete operations into current document counts."""

    current: dict[str, set[str]] = defaultdict(set)
    rows = 0
    for row in iter_ledger(path):
        rows += 1
        collection = str(row.get("collection") or "")
        doc_id = str(row.get("doc_id") or "")
        if not collection or not doc_id:
            continue
        if str(row.get("op") or "add") == "delete":
            current[collection].discard(doc_id)
        else:
            current[collection].add(doc_id)
    return rows, {collection: len(ids) for collection, ids in current.items()}


def build_inventory(workspace_root: Path, chroma_root: Path | None = None) -> list[SourceInventory]:
    """Build an inventory from the registry, ledgers, and filesystem sizes."""

    root = chroma_root or workspace_root
    size_cache: dict[Path, int] = {}

    def cached_size(path: Path) -> int:
        resolved = path.resolve()
        if resolved not in size_cache:
            size_cache[resolved] = _directory_size(resolved)
        return size_cache[resolved]

    ledger_stats: dict[tuple[str, str], tuple[int, int]] = {}
    for ledger in sorted(workspace_root.glob("*/.source_ledger.jsonl")):
        rows, counts = ledger_counts(ledger)
        for collection, count in counts.items():
            ledger_stats[(ledger.parent.name, collection)] = (rows, count)

    inventory: list[SourceInventory] = []
    seen: set[tuple[str, str, str]] = set()
    for space_key, space in MEMORY_SPACES.items():
        for location in space.legacy:
            physical = location.table or f"{location.kb_name}/{location.collection}"
            identity = (location.backend, physical, space_key)
            if identity in seen:
                continue
            seen.add(identity)
            stats = ledger_stats.get((location.kb_name or "", location.collection or ""))
            data_path = root / str(location.kb_name) if location.kb_name else None
            bytes_on_disk = cached_size(data_path) if data_path and data_path.exists() else None
            warnings: list[str] = []
            if location.backend == "chroma" and stats is None:
                warnings.append("no source-ledger count; direct export required before migration")
            if location.source_kind == "projection":
                warnings.append("projection: preserve/recover the authoritative source URI")
            inventory.append(
                SourceInventory(
                    source_key=f"{location.backend}:{physical}",
                    backend=location.backend,
                    location=physical,
                    source_kind=location.source_kind,
                    target_space=space_key,
                    target_durability=space.durability.value,
                    ledger_rows=stats[0] if stats else None,
                    live_documents=stats[1] if stats else None,
                    bytes_on_disk=bytes_on_disk,
                    warnings=tuple(warnings),
                )
            )

    # Include ledger collections that are dynamic or not yet registered.
    registered_locations = {
        (item.location.split("/", 1)[0], item.location.split("/", 1)[1])
        for item in inventory
        if item.backend == "chroma" and "/" in item.location
    }
    for (kb_name, collection), (rows, count) in sorted(ledger_stats.items()):
        if (kb_name, collection) in registered_locations:
            continue
        classified = classify_collection(collection)
        dynamic_warnings: tuple[str, ...] = (
            () if classified else ("unclassified legacy collection",)
        )
        inventory.append(
            SourceInventory(
                source_key=f"chroma:{kb_name}/{collection}",
                backend="chroma",
                location=f"{kb_name}/{collection}",
                source_kind="canonical" if classified and classified.startswith("operational.") else "unknown",
                target_space=classified or "unclassified",
                target_durability=(
                    MEMORY_SPACES[classified].durability.value
                    if classified
                    else "unknown"
                ),
                ledger_rows=rows,
                live_documents=count,
                bytes_on_disk=cached_size(root / kb_name) if (root / kb_name).exists() else None,
                warnings=dynamic_warnings,
            )
        )
    return inventory


def inventory_as_dict(items: Iterable[SourceInventory]) -> dict[str, object]:
    rows = [asdict(item) for item in items]
    return {
        "format_version": 1,
        "sources": rows,
        "summary": {
            "source_count": len(rows),
            "unclassified_count": sum(row["target_space"] == "unclassified" for row in rows),
            "durable_source_count": sum(row["target_durability"] == Durability.DURABLE.value for row in rows),
            "operational_source_count": sum(row["target_durability"] == Durability.OPERATIONAL.value for row in rows),
        },
    }


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total
