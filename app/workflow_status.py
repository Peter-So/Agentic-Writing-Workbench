from __future__ import annotations

from datetime import datetime
from typing import Any


MATERIAL_ASSEMBLY_SUBSTAGES = [
    "creative_state", "methodology_context", "creative_enhancements",
    "material_profile", "context_broker",
]

STAGE_PRESETS: dict[str, list[str]] = {
    "draft": [
        "request_analyze", "need_audit", "draft_assemble",
        "creative_state", "methodology_context", "creative_enhancements",
        "material_profile", "context_broker", "prompt_refine", "provider_route", "generate", "pre_review",
        "model_review", "draft_finalize", "user_confirm",
    ],
    "provider": [
        "provider_fanout", "provider_confirm_gate", "provider_consensus",
        "provider_digest", "provider_merge",
    ],
    "followup": [
        "request_analyze", "need_audit", "context_followup", "provider_route",
        "generate", "pre_review", "model_review", "draft_finalize",
        "user_confirm",
    ],
    "provider_confirm": [
        "provider_confirm_gate", "provider_consensus", "provider_digest",
        "provider_merge", "generate", "pre_review", "model_review",
        "draft_finalize", "user_confirm",
    ],
    "intervention": [
        "submit", "memory_lookup", "llm_analysis", "knowledge_settle",
        "memory_write", "policy_update", "impact_analyze", "primary_write",
        "primary_artifact", "related_write", "related_pending",
        "invocation_finalize", "pending_clear", "cleanup", "complete",
    ],
    "reference_import": [
        "reference_import_validate", "reference_import_save",
        "reference_import_analyze", "reference_import_five_dim",
        "reference_import_index", "reference_import_refresh",
    ],
    "archive": [
        "archive_submit", "archive_write", "overwrite_confirm", "overwrite",
        "archive_refresh", "complete",
    ],
}

STAGE_LABELS: dict[str, str] = {
    "request_analyze": "请求理解",
    "need_audit": "需求审计",
    "context_followup": "上下文续问",
    "draft_assemble": "材料装配",
    "creative_state": "状态卡/伏笔",
    "methodology_context": "方法论匹配",
    "creative_enhancements": "增强卡片",
    "material_profile": "阶段裁剪",
    "context_broker": "上下文调度",
    "prompt_refine": "专业提问",
    "provider_route": "路由决策",
    "provider_fanout": "网页模型",
    "provider_confirm_gate": "确认材料",
    "provider_consensus": "共识归纳",
    "provider_digest": "多维评分",
    "provider_merge": "融合生成",
    "generate": "融合成稿",
    "pre_review": "规则预审",
    "model_review": "模型审查",
    "draft_finalize": "定稿",
    "user_confirm": "用户确认",
    "submit": "提交中",
    "memory_lookup": "记忆查找",
    "llm_analysis": "LLM 分析中",
    "knowledge_settle": "知识沉淀",
    "memory_write": "长期记忆写入",
    "policy_update": "采纳策略更新",
    "impact_analyze": "影响范围分析",
    "primary_write": "主要文件改写",
    "primary_artifact": "结构文件保存",
    "related_write": "关联文件改写",
    "related_pending": "关联文件待确认",
    "invocation_finalize": "任务状态归档",
    "pending_clear": "清理待确认记忆",
    "cleanup": "清理临时缓存",
    "complete": "完成",
    "reference_import_validate": "校验上传",
    "reference_import_save": "保存原文",
    "reference_import_analyze": "五维抽取",
    "reference_import_five_dim": "写入多维参考库",
    "reference_import_index": "重建索引",
    "reference_import_refresh": "刷新盘点",
    "archive_submit": "提交归档",
    "archive_write": "写入归档",
    "overwrite_confirm": "等待覆盖确认",
    "overwrite": "覆盖写回",
    "archive_refresh": "刷新项目状态",
}


def stage_preset(name: str) -> list[str]:
    return list(STAGE_PRESETS.get(name) or STAGE_PRESETS["draft"])


def draft_stages(*, use_provider_source: bool = False, selected_provider: bool = False,
                 followup: bool = False) -> list[str]:
    base = stage_preset("followup" if followup else "draft")
    if not (use_provider_source and selected_provider):
        return base
    route_index = base.index("provider_route") if "provider_route" in base else -1
    if route_index < 0:
        return base
    return [*base[:route_index + 1], *stage_preset("provider"), *base[route_index + 1:]]


def workflow_snapshot(
    *,
    stages: list[str],
    current: str,
    done: list[str] | None = None,
    durations_ms: dict[str, int] | None = None,
    invocation_id: str = "",
    task: str = "",
    chapter: int | None = None,
    track: str = "create",
    status: str = "running",
    source: str = "backend",
) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    return normalize_workflow_status({
        "stages": stages,
        "current": current,
        "done": done or [],
        "durations_ms": durations_ms or {},
        "stage_started_at": now,
        "status": status,
        "source": source,
        "updated_at": now,
        "invocation_id": invocation_id,
        "task": task,
        "chapter": chapter,
        "track": track,
    })


def normalize_workflow_status(status: dict[str, Any]) -> dict[str, Any]:
    """Normalize recoverable workflow snapshots across old and current UI contracts.

    The material enhancement cards are sub-stages inside the LangGraph
    `draft_assemble` node. Older snapshots only marked the parent node done, so
    restoring them left the sub-stage chips gray even though their data had
    already been assembled. Treat a completed parent as completion of those
    sub-stages for display/recovery purposes.
    """
    if not isinstance(status, dict):
        return {}
    out = dict(status)
    stages = [str(item or "").strip() for item in out.get("stages") or [] if str(item or "").strip()]
    if "provider_confirm_gate" in stages and "request_analyze" not in stages:
        stages = draft_stages(use_provider_source=True, selected_provider=True)
    stages = _normalize_stage_order(stages)
    done = [str(item or "").strip() for item in out.get("done") or [] if str(item or "").strip()]
    current = str(out.get("current") or "").strip()
    if out.get("status") == "awaiting_confirm" and (current == "user_confirm" or "draft_finalize" in done):
        current = "user_confirm"
        done = [node for node in done if node != "user_confirm"]
        durations = out.get("durations_ms")
        if isinstance(durations, dict):
            durations.pop("user_confirm", None)
        out.pop("total_ms", None)
    if "provider_confirm_gate" in stages and "request_analyze" in stages and any(
        node in done or out.get("current") == node for node in {"provider_confirm_gate", "provider_consensus", "provider_digest", "provider_merge", "generate", "pre_review", "model_review", "draft_finalize", "user_confirm"}
    ):
        gate_index = stages.index("provider_confirm_gate")
        for node in stages[:gate_index]:
            if node not in done:
                done.append(node)
    if "draft_assemble" in done:
        for node in MATERIAL_ASSEMBLY_SUBSTAGES:
            if node in stages and node not in done:
                done.append(node)
    if any(node in done for node in {"provider_merge", "generate", "pre_review", "model_review", "draft_finalize", "user_confirm"}):
        for node in ("provider_consensus", "provider_digest", "provider_merge"):
            if node in stages and node not in done:
                done.append(node)
    if current and current in done:
        try:
            idx = stages.index(current)
            next_node = next((node for node in stages[idx + 1:] if node not in done), "")
            if next_node:
                current = next_node
        except ValueError:
            pass
    if not current:
        current = next((node for node in stages if node not in done), "")
    if current:
        out["current"] = current
    if current in {"provider_confirm_gate", "user_confirm", "overwrite_confirm"} and current not in done:
        out["status"] = "awaiting_confirm"
    out["stages"] = stages
    out["done"] = [node for node in done if node in stages]
    return out


def _normalize_stage_order(stages: list[str]) -> list[str]:
    if "draft_assemble" not in stages:
        return stages
    canonical = draft_stages(
        use_provider_source="provider_fanout" in stages,
        selected_provider="provider_fanout" in stages,
        followup=False,
    )
    order = {node: idx for idx, node in enumerate(canonical)}
    return sorted(stages, key=lambda node: (order.get(node, len(order) + stages.index(node)), stages.index(node)))


def save_pending_workflow_snapshot(
    *,
    novel_id: str | None,
    track: str,
    invocation_id: str,
    stages: list[str],
    current: str,
    done: list[str] | None = None,
    status: str = "running",
    task: str = "",
    chapter: int | None = None,
    source: str = "backend",
) -> dict[str, Any]:
    if not invocation_id:
        return {"ok": True, "updated": False, "reason": "missing_invocation_id"}
    try:
        from app.pending_intent_memory import update_pending_workflow_status

        return update_pending_workflow_status(
            novel_id=novel_id,
            track=track,
            invocation_id=invocation_id,
            workflow_status=workflow_snapshot(
                stages=stages,
                current=current,
                done=done,
                invocation_id=invocation_id,
                task=task,
                chapter=chapter,
                track=track,
                status=status,
                source=source,
            ),
        )
    except Exception as exc:
        return {"ok": False, "updated": False, "error": f"{type(exc).__name__}: {exc}"}
