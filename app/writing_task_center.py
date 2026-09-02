from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import normalize_novel_id
from app.project_paths import logs_invocations_dir


TASK_LOG = ROOT / "data" / "writing_task_center" / "tasks.json"


def task_center(novel_id: str | None, *, limit: int = 30) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    items: list[dict[str, Any]] = []
    items.extend(_invocation_items(nid, limit=limit))
    items.extend(_pending_items(nid))
    items.extend(_runtime_task_items(nid))
    items.extend(_upgrade_items(nid))
    items = _dedupe(items)
    items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    summary: dict[str, int] = {}
    for item in items:
        key = str(item.get("type") or "unknown")
        summary[key] = summary.get(key, 0) + 1
    return {
        "ok": True,
        "novel_id": nid,
        "summary": summary,
        "items": items[:limit],
    }


def record_task_event(
    *,
    task_type: str,
    task_id: str,
    status: str,
    label: str,
    novel_id: str | None = None,
    source: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    payload = _read_log()
    items = payload.setdefault("items", {})
    key = f"{task_type}:{task_id}"
    now = _now()
    existing = items.get(key) if isinstance(items.get(key), dict) else {}
    item = {
        **existing,
        "id": task_id,
        "type": task_type,
        "status": status,
        "label": label,
        "novel_id": nid,
        "source": source or task_type,
        "details": details or existing.get("details") or {},
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "next_action": _next_action(task_type, status),
    }
    items[key] = item
    _write_log(payload)
    return item


def _invocation_items(novel_id: str, *, limit: int) -> list[dict[str, Any]]:
    try:
        from app.writing_invocations import list_recent_invocations

        records = list_recent_invocations(novel_id, limit=limit)
    except Exception:
        records = []
    out: list[dict[str, Any]] = []
    for record in records:
        invocation_id = str(record.get("id") or "")
        if not invocation_id:
            continue
        current_node = str(record.get("current_node") or "")
        status = str(record.get("status") or "unknown")
        out.append({
            "id": invocation_id,
            "type": "invocation",
            "status": status,
            "label": f"{record.get('task') or 'writing'} · {current_node or status}",
            "novel_id": novel_id,
            "source": "invocation_log",
            "created_at": record.get("created_at") or "",
            "updated_at": record.get("updated_at") or "",
            "next_action": _next_action("invocation", status),
            "details": {
                "task": record.get("task") or "",
                "mode": record.get("mode") or "",
                "chapter": record.get("chapter"),
                "current_node": current_node,
                "trajectory_count": len(record.get("trajectory") or []),
            },
        })
    return out


def _pending_items(novel_id: str) -> list[dict[str, Any]]:
    try:
        from app.pending_intent_memory import latest_pending_workflow_status, recover_pending_intent

        pending = recover_pending_intent(novel_id=novel_id, track="create")
        workflow = latest_pending_workflow_status(novel_id=novel_id, track="create")
    except Exception:
        pending = None
        workflow = {}
    out: list[dict[str, Any]] = []
    if isinstance(pending, dict) and pending.get("id"):
        out.append({
            "id": str(pending.get("id") or pending.get("invocation_id") or ""),
            "type": "pending_intent",
            "status": str(pending.get("status") or "pending"),
            "label": f"{pending.get('task') or 'pending'} · 等待继续",
            "novel_id": novel_id,
            "source": "pending_intent",
            "created_at": pending.get("created_at") or "",
            "updated_at": pending.get("updated_at") or "",
            "next_action": "恢复对话框并继续当前任务",
            "details": {
                "invocation_id": pending.get("invocation_id") or "",
                "task": pending.get("task") or "",
                "chapter": pending.get("chapter"),
            },
        })
    data = workflow.get("workflow_status") if isinstance(workflow, dict) else {}
    if isinstance(data, dict) and data.get("stages"):
        status = str(data.get("status") or "running")
        out.append({
            "id": str(data.get("invocation_id") or "workflow"),
            "type": "workflow_status",
            "status": status,
            "label": f"{data.get('task') or 'workflow'} · {data.get('current') or status}",
            "novel_id": novel_id,
            "source": "pending_workflow_status",
            "created_at": "",
            "updated_at": data.get("updated_at") or "",
            "next_action": _next_action("workflow_status", status),
            "details": {
                "current": data.get("current") or "",
                "done_count": len(data.get("done") or []),
                "stage_count": len(data.get("stages") or []),
                "total_ms": data.get("total_ms"),
            },
        })
    return out


def _runtime_task_items(novel_id: str) -> list[dict[str, Any]]:
    payload = _read_log()
    items = payload.get("items") if isinstance(payload, dict) else {}
    if not isinstance(items, dict):
        return []
    out = []
    for item in items.values():
        if not isinstance(item, dict):
            continue
        if item.get("novel_id") in {novel_id, "", None}:
            out.append(dict(item))
    return out


def _upgrade_items(novel_id: str) -> list[dict[str, Any]]:
    # Agentic-Writing-Workbench has a web update task in the public repo. The
    # writing development project may not install that module, so expose an
    # explicit non-blocking state instead of fabricating a task.
    marker = ROOT / "data" / "workbench_update" / "latest.json"
    if not marker.is_file():
        return [{
            "id": "workbench_update",
            "type": "upgrade_task",
            "status": "not_configured",
            "label": "公开库更新 · 未接入当前开发项目",
            "novel_id": novel_id,
            "source": "optional_update_marker",
            "created_at": "",
            "updated_at": "",
            "next_action": "仅公开库发行版启用 Web 更新闭环",
            "details": {},
        }]
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return [{
        "id": str(data.get("id") or "workbench_update"),
        "type": "upgrade_task",
        "status": str(data.get("status") or "unknown"),
        "label": str(data.get("label") or "公开库更新"),
        "novel_id": novel_id,
        "source": "workbench_update_marker",
        "created_at": data.get("created_at") or "",
        "updated_at": data.get("updated_at") or "",
        "next_action": data.get("next_action") or _next_action("upgrade_task", str(data.get("status") or "")),
        "details": data.get("details") or {},
    }]


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (str(item.get("type") or ""), str(item.get("id") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _next_action(task_type: str, status: str) -> str:
    if status in {"running", "queued"}:
        return "等待任务完成或继续轮询状态"
    if status in {"pending", "awaiting_user", "awaiting_overwrite_confirm"}:
        return "等待用户确认后继续"
    if status in {"failed", "error", "interrupted"}:
        return "查看错误后重试或回滚"
    if status == "skipped":
        return "已跳过重复任务，可查看已有结果"
    if status in {"completed", "success", "done"}:
        return "可查看轨迹、复盘或归档产物"
    if status == "not_configured":
        return "当前项目未启用该任务来源"
    return "查看详情"


def _read_log() -> dict[str, Any]:
    if not TASK_LOG.is_file():
        return {"items": {}}
    try:
        data = json.loads(TASK_LOG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"items": {}}
    except Exception:
        return {"items": {}}


def _write_log(payload: dict[str, Any]) -> None:
    TASK_LOG.parent.mkdir(parents=True, exist_ok=True)
    TASK_LOG.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
