from __future__ import annotations

from typing import Any

from app.novel_context import normalize_novel_id
from app.writing_invocations import list_recent_invocations


def recall_eval(novel_id: str, limit: int = 20) -> dict[str, Any]:
    """Lightweight proxy for whether collected materials entered the loop.

    This is not a semantic truth score. It only checks observable traces:
    request file, provider confirmation, merge/finalize actions and artifacts.
    """
    nid = normalize_novel_id(novel_id)
    records = list_recent_invocations(nid, limit=limit)
    items = [_score_record(record) for record in records]
    summary = {
        "invocations": len(items),
        "with_request_file": sum(1 for item in items if item["signals"]["request_file"]),
        "with_provider_answers": sum(1 for item in items if item["signals"]["provider_answers"]),
        "with_merge": sum(1 for item in items if item["signals"]["merge_action"]),
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
    events = record.get("events") or []
    actions_text = " ".join(
        str((event.get("details") or {}).get("actions", ""))
        for event in events
        if isinstance(event, dict)
    )
    provider_count = len(record.get("providers") or {})
    request_file = bool(artifacts.get("request_file") or _event_detail(record, "request_file"))
    provider_answers = provider_count > 0 or bool(artifacts.get("provider_answers"))
    merge_action = "merge(" in actions_text or "digest(" in actions_text
    harness_blocked = any(_has_harness_issue(item) for item in record.get("harness") or [])
    completed = record.get("status") == "completed"
    score = 0
    if request_file:
        score += 0.2
    if provider_answers:
        score += 0.25
    if merge_action:
        score += 0.25
    if completed:
        score += 0.2
    if not harness_blocked:
        score += 0.1
    return {
        "id": record.get("id", ""),
        "task": record.get("task", ""),
        "status": record.get("status", ""),
        "created_at": record.get("created_at", ""),
        "proxy_score": round(score, 2),
        "signals": {
            "request_file": request_file,
            "provider_answers": provider_answers,
            "merge_action": merge_action,
            "completed": completed,
            "harness_blocked": harness_blocked,
        },
    }


def _event_detail(record: dict[str, Any], key: str) -> Any:
    for event in reversed(record.get("events") or []):
        if not isinstance(event, dict):
            continue
        details = event.get("details") or {}
        if key in details:
            return details.get(key)
    return None


def _has_harness_issue(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    issues = item.get("issues") or (item.get("result") or {}).get("issues") or []
    return bool(issues)
