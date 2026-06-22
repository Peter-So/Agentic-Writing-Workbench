from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import novel_dir, normalize_novel_id
from app.project_paths import logs_invocations_dir


def new_invocation_id() -> str:
    return f"wr_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def prompt_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def begin_invocation(
    *,
    novel_id: str,
    track: str,
    mode: str,
    task: str,
    chapter: int | None,
    user_message: str,
    use_provider_source: bool,
    login_confirmed: dict[str, bool] | None = None,
    workflow_sop: dict[str, Any] | None = None,
    invocation_id: str | None = None,
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    inv_id = invocation_id or new_invocation_id()
    now = _now()
    record = {
        "id": inv_id,
        "novel_id": nid,
        "track": track,
        "mode": mode,
        "task": task,
        "chapter": chapter,
        "status": "running",
        "current_node": "draft_stream",
        "use_provider_source": bool(use_provider_source),
        "selected_providers": [
            provider for provider, enabled in (login_confirmed or {}).items() if enabled
        ],
        "user_message_preview": (user_message or "")[:500],
        "user_message_hash": prompt_hash(user_message or ""),
        "workflow_sop": workflow_sop or {},
        "created_at": now,
        "updated_at": now,
        "events": [],
        "trajectory": [],
        "budgets": [],
        "harness": [],
        "routes": [],
        "providers": {},
        "artifacts": {},
    }
    _write(nid, inv_id, record)
    append_event(nid, inv_id, "invocation_started", "创作任务启动", status="running")
    return record


def append_event(
    novel_id: str | None,
    invocation_id: str | None,
    event: str,
    label: str,
    *,
    node: str | None = None,
    status: str | None = None,
    details: dict[str, Any] | None = None,
    provider: str | None = None,
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not novel_id or not invocation_id:
        return None
    nid = normalize_novel_id(novel_id)
    record = _read(nid, invocation_id)
    if record is None:
        return None
    now = _now()
    item = {
        "at": now,
        "event": event,
        "label": label,
    }
    if node:
        item["node"] = node
        record["current_node"] = node
    if status:
        item["status"] = status
        record["status"] = status
    if provider:
        item["provider"] = provider
    if details:
        item["details"] = _jsonable(details)
    if artifacts:
        record.setdefault("artifacts", {}).update(_jsonable(artifacts))
    record.setdefault("events", []).append(item)
    record["updated_at"] = now
    _write(nid, invocation_id, record)
    return item


def append_trajectory(
    novel_id: str | None,
    invocation_id: str | None,
    node: str,
    summary: dict[str, Any],
) -> None:
    if not novel_id or not invocation_id:
        return
    nid = normalize_novel_id(novel_id)
    record = _read(nid, invocation_id)
    if record is None:
        return
    record.setdefault("trajectory", []).append({
        "at": _now(),
        "node": node,
        "summary": _jsonable(summary),
    })
    record["updated_at"] = _now()
    _write(nid, invocation_id, record)


def append_budget(
    novel_id: str | None,
    invocation_id: str | None,
    node: str,
    budget: dict[str, Any],
) -> None:
    if not novel_id or not invocation_id:
        return
    nid = normalize_novel_id(novel_id)
    record = _read(nid, invocation_id)
    if record is None:
        return
    record.setdefault("budgets", []).append({
        "at": _now(),
        "node": node,
        **_jsonable(budget),
    })
    record["updated_at"] = _now()
    _write(nid, invocation_id, record)


def append_harness(
    novel_id: str | None,
    invocation_id: str | None,
    node: str,
    result: dict[str, Any],
) -> None:
    if not novel_id or not invocation_id:
        return
    nid = normalize_novel_id(novel_id)
    record = _read(nid, invocation_id)
    if record is None:
        return
    record.setdefault("harness", []).append({
        "at": _now(),
        "node": node,
        **_jsonable(result),
    })
    record["updated_at"] = _now()
    _write(nid, invocation_id, record)


def append_route(
    novel_id: str | None,
    invocation_id: str | None,
    node: str,
    route: dict[str, Any],
) -> None:
    if not novel_id or not invocation_id:
        return
    nid = normalize_novel_id(novel_id)
    record = _read(nid, invocation_id)
    if record is None:
        return
    record.setdefault("routes", []).append({
        "at": _now(),
        "node": node,
        **_jsonable(route),
    })
    record["updated_at"] = _now()
    _write(nid, invocation_id, record)


def update_provider(
    novel_id: str | None,
    invocation_id: str | None,
    provider: str | None,
    *,
    name: str | None = None,
    status: str,
    result: str | None = None,
    elapsed_seconds: float | None = None,
) -> None:
    if not novel_id or not invocation_id or not provider:
        return
    nid = normalize_novel_id(novel_id)
    record = _read(nid, invocation_id)
    if record is None:
        return
    info = dict(record.setdefault("providers", {}).get(provider) or {})
    info.update({"provider": provider, "status": status, "updated_at": _now()})
    if name:
        info["name"] = name
    if elapsed_seconds is not None:
        info["elapsed_seconds"] = elapsed_seconds
    if result is not None:
        info["result_length"] = len(result)
        info["result_hash"] = prompt_hash(result)
    record["providers"][provider] = info
    record["updated_at"] = _now()
    _write(nid, invocation_id, record)


def finish_invocation(
    novel_id: str | None,
    invocation_id: str | None,
    *,
    status: str,
    label: str,
    details: dict[str, Any] | None = None,
    artifacts: dict[str, Any] | None = None,
) -> None:
    append_event(
        novel_id,
        invocation_id,
        "invocation_finished",
        label,
        status=status,
        details=details,
        artifacts=artifacts,
    )


def get_invocation(novel_id: str, invocation_id: str) -> dict[str, Any] | None:
    return _read(normalize_novel_id(novel_id), invocation_id)


def list_recent_invocations(novel_id: str, limit: int = 5) -> list[dict[str, Any]]:
    nid = normalize_novel_id(novel_id)
    root = logs_invocations_dir(nid)
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        data = _read(nid, path.stem)
        if data:
            records.append(data)
        if len(records) >= limit:
            break
    return records


def cost_board(novel_id: str, limit: int = 20) -> dict[str, Any]:
    records = list_recent_invocations(novel_id, limit=limit)
    items: list[dict[str, Any]] = []
    totals = {
        "invocations": len(records),
        "estimated_total_tokens": 0,
        "fanout_prompt_tokens_est": 0,
        "provider_runs": 0,
        "fanout_routes": 0,
        "single_agent_routes": 0,
        "warn_count": 0,
        "error_count": 0,
    }
    by_reason: dict[str, int] = {}
    for record in records:
        budgets = record.get("budgets") or []
        routes = record.get("routes") or []
        budget = _latest_dict(budgets)
        route = _latest_dict(routes)
        providers = record.get("providers") or {}
        provider_runs = sum(1 for item in providers.values() if item.get("status") in {"success", "partial", "failed", "completed"})
        estimated_total = int(budget.get("estimated_total_tokens") or 0)
        fanout_prompt = int(budget.get("fanout_prompt_tokens_est") or 0)
        decision = route.get("decision") or ("fanout" if provider_runs else "single_agent")
        reason = route.get("reason") or "unknown"
        level = budget.get("level") or "ok"
        totals["estimated_total_tokens"] += estimated_total
        totals["fanout_prompt_tokens_est"] += fanout_prompt
        totals["provider_runs"] += provider_runs
        if decision == "fanout":
            totals["fanout_routes"] += 1
        elif decision == "single_agent":
            totals["single_agent_routes"] += 1
        if level == "warn":
            totals["warn_count"] += 1
        if level == "error":
            totals["error_count"] += 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
        items.append({
            "id": record.get("id", ""),
            "status": record.get("status", ""),
            "task": record.get("task", ""),
            "mode": record.get("mode", ""),
            "created_at": record.get("created_at", ""),
            "updated_at": record.get("updated_at", ""),
            "route": {
                "decision": decision,
                "reason": reason,
                "boundary": route.get("boundary", ""),
                "provider_count": route.get("provider_count", len(record.get("selected_providers") or [])),
                "selected_providers": route.get("selected_providers") or record.get("selected_providers") or [],
            },
            "budget": budget,
            "provider_runs": provider_runs,
            "trajectory_count": len(record.get("trajectory") or []),
            "harness_count": len(record.get("harness") or []),
        })
    totals["by_reason"] = by_reason
    totals["average_estimated_tokens"] = round(totals["estimated_total_tokens"] / len(records)) if records else 0
    return {
        "ok": True,
        "novel_id": normalize_novel_id(novel_id),
        "limit": limit,
        "summary": totals,
        "items": items,
    }


def invocation_rel_path(novel_id: str, invocation_id: str) -> str:
    path = _path(normalize_novel_id(novel_id), invocation_id)
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _path(novel_id: str, invocation_id: str) -> Path:
    return logs_invocations_dir(novel_id) / f"{invocation_id}.json"


def _read(novel_id: str, invocation_id: str) -> dict[str, Any] | None:
    path = _path(novel_id, invocation_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(novel_id: str, invocation_id: str, record: dict[str, Any]) -> None:
    path = _path(novel_id, invocation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_jsonable(record), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _latest_dict(items: list[Any]) -> dict[str, Any]:
    for item in reversed(items):
        if isinstance(item, dict):
            return item
    return {}


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(v) for v in value]
        return str(value)
