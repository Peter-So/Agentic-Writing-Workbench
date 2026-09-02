from __future__ import annotations

from typing import Any

from app.novel_context import normalize_novel_id
from app.writing_invocations import list_recent_invocations


def recall_eval(novel_id: str, limit: int = 20) -> dict[str, Any]:
    """Lightweight proxy for whether collected materials entered the loop.

    This is not a semantic truth score. It only checks observable traces:
    recorded graph trajectory, finalized draft artifacts, and completion state.
    """
    nid = normalize_novel_id(novel_id)
    records = list_recent_invocations(nid, limit=limit)
    items = [_score_record(record) for record in records]
    summary = {
        "invocations": len(items),
        "with_trajectory": sum(1 for item in items if item["signals"]["trajectory"]),
        "with_draft_artifact": sum(1 for item in items if item["signals"]["draft_artifact"]),
        "completed": sum(1 for item in items if item["status"] == "completed"),
        "average_proxy_score": round(sum(item["proxy_score"] for item in items) / len(items), 2) if items else 0,
    }
    return {
        "ok": True,
        "novel_id": nid,
        "limit": limit,
        "label": "lightweight_proxy_not_semantic_truth",
        "summary": summary,
        "items": items,
    }


def _score_record(record: dict[str, Any]) -> dict[str, Any]:
    artifacts = record.get("artifacts") or {}
    trajectory = bool(record.get("trajectory"))
    draft_artifact = bool(artifacts.get("draft"))
    completed = record.get("status") == "completed"
    score = 0
    if trajectory:
        score += 0.4
    if draft_artifact:
        score += 0.4
    if completed:
        score += 0.2
    return {
        "id": record.get("id", ""),
        "task": record.get("task", ""),
        "status": record.get("status", ""),
        "created_at": record.get("created_at", ""),
        "proxy_score": round(score, 2),
        "signals": {
            "trajectory": trajectory,
            "draft_artifact": draft_artifact,
            "completed": completed,
        },
    }
