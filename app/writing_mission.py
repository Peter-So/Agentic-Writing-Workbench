from __future__ import annotations

from pathlib import Path
from typing import Any

from app.novel_context import novel_dir, normalize_novel_id
from app.project_kinds import project_kind
from app.writing_invocations import cost_board, list_recent_invocations
from app.writing_sop import sop_summary


MISSION_STAGES = ["idea", "spec", "drafting", "review", "accepted", "archived"]


def mission_hub(novel_id: str, limit: int = 10) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    root = novel_dir(nid)
    kind = project_kind(nid)
    recent = list_recent_invocations(nid, limit=limit)
    board = cost_board(nid, limit=20)
    artifacts = _artifact_status(root, kind)
    active_stage = _active_stage(recent, artifacts)
    return {
        "ok": True,
        "novel_id": nid,
        "project_kind": kind,
        "active_stage": active_stage,
        "stages": [
            {
                "id": stage,
                "label": _stage_label(stage),
                "status": _stage_status(stage, active_stage, recent, artifacts),
            }
            for stage in MISSION_STAGES
        ],
        "sop": sop_summary(kind),
        "artifacts": artifacts,
        "recent": [_mission_item(item) for item in recent],
        "cost_summary": board.get("summary") or {},
        "blocking": _blocking_items(recent),
    }


def _artifact_status(root: Path, kind: str) -> dict[str, Any]:
    files: list[str] = []
    try:
        from app.project_structure import load_project_structure

        structure = load_project_structure(root.name)
        files.extend(
            item.get("path")
            for item in (structure.get("documents") or {}).values()
            if item.get("path")
        )
    except Exception:
        files = []
    if kind == "novel_strong":
        files = ["project.yaml", "正文", "提示词", "脚本", *files]
    if not files:
        files = ["project.yaml", "维基/项目结构.md"]
    items = []
    for name in files:
        path = root / name
        if path.is_dir():
            exists = True
            size = sum(1 for _ in path.rglob("*"))
        else:
            exists = path.is_file()
            size = path.stat().st_size if exists else 0
        items.append({"name": name, "exists": exists, "size": size})
    return {
        "root_exists": root.exists(),
        "files": items,
        "ready_count": sum(1 for item in items if item["exists"] and item["size"] > 0),
        "total_count": len(items),
    }


def _active_stage(recent: list[dict[str, Any]], artifacts: dict[str, Any]) -> str:
    if recent:
        latest = recent[0]
        if latest.get("status") == "awaiting_confirm":
            return "review"
        if latest.get("status") == "running":
            return "drafting"
        if latest.get("status") == "failed":
            return "drafting"
        if latest.get("status") == "completed":
            return "accepted"
    if artifacts.get("ready_count", 0) > 1:
        return "spec"
    return "idea"


def _stage_status(stage: str, active: str, recent: list[dict[str, Any]], artifacts: dict[str, Any]) -> str:
    order = {name: index for index, name in enumerate(MISSION_STAGES)}
    if stage == active:
        return "active"
    if order[stage] < order[active]:
        return "done"
    if stage == "spec" and artifacts.get("ready_count", 0):
        return "done"
    if stage == "drafting" and recent:
        return "done"
    return "pending"


def _mission_item(record: dict[str, Any]) -> dict[str, Any]:
    routes = record.get("routes") or []
    budgets = record.get("budgets") or []
    return {
        "id": record.get("id", ""),
        "status": record.get("status", ""),
        "task": record.get("task", ""),
        "mode": record.get("mode", ""),
        "created_at": record.get("created_at", ""),
        "updated_at": record.get("updated_at", ""),
        "current_node": record.get("current_node", ""),
        "route": routes[-1] if routes else {},
        "budget": budgets[-1] if budgets else {},
        "trajectory_count": len(record.get("trajectory") or []),
        "harness_count": len(record.get("harness") or []),
    }


def _blocking_items(recent: list[dict[str, Any]]) -> list[dict[str, str]]:
    blockers = []
    for record in recent[:5]:
        if record.get("status") == "failed":
            blockers.append({"level": "error", "message": f"{record.get('id')} 执行失败", "id": record.get("id", "")})
        for item in record.get("harness") or []:
            if item.get("level") == "error":
                blockers.append({"level": "error", "message": "Prompt harness 阻断", "id": record.get("id", "")})
    return blockers[:5]


def _stage_label(stage: str) -> str:
    return {
        "idea": "想法",
        "spec": "材料/规格",
        "drafting": "生成中",
        "review": "确认/审查",
        "accepted": "已采纳",
        "archived": "归档",
    }.get(stage, stage)
