from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from app.config import ROOT
from app.novel_context import normalize_novel_id
from app.writing_invocations import list_recent_invocations


Check = Callable[[dict[str, Any]], tuple[bool, str]]


def run_writing_benchmark(novel_id: str, limit: int = 10) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    records = list_recent_invocations(nid, limit=limit)
    checks: list[tuple[str, Check]] = [
        ("terminal_status", _terminal_status),
        ("trajectory_recorded", _trajectory_recorded),
        ("bounded_iterations", _bounded_iterations),
        ("no_unconfirmed_memory_write", _no_unconfirmed_memory_write),
        ("artifact_paths_exist", _artifact_paths_exist),
    ]
    rows = []
    totals = {"passed": 0, "failed": 0, "records": len(records)}
    for record in records:
        results = []
        for name, fn in checks:
            ok, message = fn(record)
            results.append({"check": name, "ok": ok, "message": message})
            totals["passed" if ok else "failed"] += 1
        rows.append({
            "id": record.get("id", ""),
            "status": record.get("status", ""),
            "task": record.get("task", ""),
            "created_at": record.get("created_at", ""),
            "checks": results,
            "ok": all(item["ok"] for item in results),
        })
    return {
        "ok": totals["failed"] == 0,
        "novel_id": nid,
        "summary": totals,
        "benchmarks": [name for name, _ in checks],
        "records": rows,
    }


def _terminal_status(record: dict[str, Any]) -> tuple[bool, str]:
    status = record.get("status", "")
    ok = status in {"completed", "failed", "awaiting_confirm"}
    return ok, f"status={status or 'missing'}"


def _trajectory_recorded(record: dict[str, Any]) -> tuple[bool, str]:
    count = len(record.get("trajectory") or [])
    if record.get("status") == "failed":
        return True, f"failed record trajectory={count}"
    return count > 0, f"trajectory={count}"


def _bounded_iterations(record: dict[str, Any]) -> tuple[bool, str]:
    # Invocation log does not store final iterations directly; infer repeated generate nodes.
    generates = sum(1 for item in record.get("trajectory") or [] if item.get("node") == "generate")
    return generates <= 3, f"generate_nodes={generates}"


def _no_unconfirmed_memory_write(record: dict[str, Any]) -> tuple[bool, str]:
    actions = []
    for item in record.get("trajectory") or []:
        summary = item.get("summary") or {}
        value = summary.get("actions")
        if isinstance(value, dict):
            actions.extend(value.get("sample") or [])
        elif isinstance(value, list):
            actions.extend(value)
    bad = [a for a in actions if str(a).startswith("summarize_chapter(") or str(a).startswith("save_setting(")]
    return not bad, f"unconfirmed_memory_actions={bad[:3]}"


def _artifact_paths_exist(record: dict[str, Any]) -> tuple[bool, str]:
    missing = []
    artifacts = record.get("artifacts") or {}
    for value in artifacts.values():
        for path in _iter_paths(value):
            candidate = ROOT / path
            if not candidate.exists():
                missing.append(path)
    return not missing, f"missing={missing[:3]}" if missing else "artifact paths ok"


def _iter_paths(value: Any):
    if isinstance(value, str) and (value.startswith("projects/") or value.startswith("data/")):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_paths(item)
