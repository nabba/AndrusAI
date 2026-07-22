import json

from app.memory_platform.inventory import (
    build_inventory,
    classify_collection,
    inventory_as_dict,
    ledger_counts,
)


def _row(collection: str, doc_id: str, op: str = "add") -> str:
    payload = {
        "collection": collection,
        "doc_id": doc_id,
        "text": "x",
        "metadata": {},
        "prev_hash": "0" * 64,
        "hash": "1" * 64,
    }
    if op != "add":
        payload["op"] = op
    return json.dumps(payload)


def test_ledger_count_folds_delete(tmp_path) -> None:
    path = tmp_path / ".source_ledger.jsonl"
    path.write_text("\n".join([_row("scope_team", "a"), _row("scope_team", "b"), _row("scope_team", "a", "delete")]))
    rows, counts = ledger_counts(path)
    assert rows == 3
    assert counts == {"scope_team": 1}


def test_dynamic_collection_classification() -> None:
    assert classify_collection("scope_agent_coder") == "operational.agent_private"
    assert classify_collection("scope_research_bb--task") == "operational.blackboard"
    assert classify_collection("scope_project_alpha") == "tenant.documents"
    assert classify_collection("not_registered") is None


def test_inventory_surfaces_unclassified_sources(tmp_path) -> None:
    ledger_dir = tmp_path / "memory"
    ledger_dir.mkdir()
    (ledger_dir / ".source_ledger.jsonl").write_text(_row("unknown_collection", "x") + "\n")
    payload = inventory_as_dict(build_inventory(tmp_path, tmp_path))
    assert payload["summary"]["unclassified_count"] == 1
