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
    timeline.sort(key=lambda item: item.get("at", ""))
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
        "artifacts": record.get("artifacts") or {},
        "workflow_sop": record.get("workflow_sop") or {},
        "raw_counts": {
            "events": len(record.get("events") or []),
            "trajectory": len(record.get("trajectory") or []),
        },
    }
