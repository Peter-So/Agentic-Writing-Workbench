from __future__ import annotations

from typing import Any

from app.novel_context import normalize_novel_id
from app.writing_invocations import get_invocation


def trajectory_review(novel_id: str, invocation_id: str) -> dict[str, Any] | None:
    record = get_invocation(normalize_novel_id(novel_id), invocation_id)
    if record is None:
        return None

    timeline: list[dict[str, Any]] = []
    for event in record.get("events") or []:
        if isinstance(event, dict):
            timeline.append({
                "at": event.get("at", ""),
                "kind": "event",
                "node": event.get("node", ""),
                "label": event.get("label") or event.get("event") or "",
                "status": event.get("status", ""),
                "details": event.get("details") or {},
            })
    for item in record.get("trajectory") or []:
        if isinstance(item, dict):
            timeline.append({
                "at": item.get("at", ""),
                "kind": "node",
                "node": item.get("node", ""),
                "label": f"{item.get('node', 'node')} 状态摘要",
                "summary": item.get("summary") or {},
            })
    for item in record.get("routes") or []:
        if isinstance(item, dict):
            timeline.append({
                "at": item.get("at", ""),
                "kind": "route",
                "node": item.get("node", "provider_route"),
                "label": f"{item.get('decision', '')} · {item.get('reason', '')}",
                "details": {
                    "boundary": item.get("boundary", ""),
                    "provider_count": item.get("provider_count", 0),
                },
            })
    for item in record.get("budgets") or []:
        if isinstance(item, dict):
            timeline.append({
                "at": item.get("at", ""),
                "kind": "budget",
                "node": item.get("node", ""),
                "label": f"预算 {item.get('level', 'ok')} · {item.get('estimated_total_tokens', 0)} tokens",
                "details": item,
            })
    for item in record.get("harness") or []:
        if isinstance(item, dict):
            issues = item.get("issues") or (item.get("result") or {}).get("issues") or []
            timeline.append({
                "at": item.get("at", ""),
                "kind": "harness",
                "node": item.get("node", ""),
                "label": f"Harness {item.get('level') or (item.get('result') or {}).get('level') or 'ok'}",
                "details": {"issues": issues},
            })

    timeline.sort(key=lambda item: item.get("at", ""))
    providers = record.get("providers") or {}
    provider_summary = [
        {
            "provider": key,
            "name": value.get("name") or key,
            "status": value.get("status", ""),
            "result_length": value.get("result_length", 0),
            "elapsed_seconds": value.get("elapsed_seconds"),
        }
        for key, value in providers.items()
        if isinstance(value, dict)
    ]
    return {
        "ok": True,
        "novel_id": normalize_novel_id(novel_id),
        "invocation": {
            "id": record.get("id", ""),
            "status": record.get("status", ""),
            "task": record.get("task", ""),
            "mode": record.get("mode", ""),
            "track": record.get("track", ""),
            "chapter": record.get("chapter"),
            "created_at": record.get("created_at", ""),
            "updated_at": record.get("updated_at", ""),
            "current_node": record.get("current_node", ""),
        },
        "timeline": timeline,
        "providers": provider_summary,
        "artifacts": record.get("artifacts") or {},
        "workflow_sop": record.get("workflow_sop") or {},
        "raw_counts": {
            "events": len(record.get("events") or []),
            "trajectory": len(record.get("trajectory") or []),
            "routes": len(record.get("routes") or []),
            "budgets": len(record.get("budgets") or []),
            "harness": len(record.get("harness") or []),
        },
    }
