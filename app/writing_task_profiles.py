from __future__ import annotations

from typing import Any

from app.project_kinds import STRONG_NOVEL_KIND


NOVEL_STAGE_PROFILES: dict[str, dict[str, Any]] = {
    "concept": {
        "id": "concept",
        "label": "概念",
        "tasks": ["logline", "brief", "materials"],
        "canonical_task": "logline",
        "structure_role": "base_setting",
        "flow": "planning_light",
        "node_flow": ["request_analyze", "draft_assemble", "provider_route", "generate", "model_review", "draft_finalize"],
        "material_sections": ["base_setting", "style_guide", "project_wiki", "writing_techniques"],
        "acceptance_signals": ["项目一句话", "类型", "基调", "核心命题", "主角", "阻碍", "故事承诺"],
    },
    "setting": {
        "id": "setting",
        "label": "基础设定",
        "tasks": ["setting"],
        "canonical_task": "setting",
        "structure_role": "base_setting",
        "flow": "planning_light",
        "node_flow": ["request_analyze", "draft_assemble", "provider_route", "generate", "model_review", "draft_finalize"],
        "material_sections": ["base_setting", "worldview", "style_guide", "project_wiki", "writing_techniques"],
        "acceptance_signals": ["项目一句话", "类型", "基调", "核心命题", "创作约束", "边界"],
    },
    "world": {
        "id": "world",
        "label": "世界观",
        "tasks": ["world", "worldview"],
        "canonical_task": "world",
        "structure_role": "worldview",
        "flow": "planning_light",
        "node_flow": ["request_analyze", "draft_assemble", "provider_route", "generate", "model_review", "draft_finalize"],
        "material_sections": ["worldview", "base_setting", "plot", "project_wiki"],
        "acceptance_signals": ["规则", "边界", "时间线", "空间", "制度", "禁忌", "剧情约束"],
    },
    "character": {
        "id": "character",
        "label": "人物",
        "tasks": ["character", "characters"],
        "canonical_task": "character",
        "structure_role": "character",
        "flow": "planning_light",
        "node_flow": ["request_analyze", "draft_assemble", "provider_route", "generate", "model_review", "draft_finalize"],
        "material_sections": ["character", "base_setting", "worldview", "plot", "project_wiki", "writing_techniques"],
        "acceptance_signals": ["姓名", "定位", "欲望", "阻碍", "关系", "声音", "弧光"],
    },
    "outline": {
        "id": "outline",
        "label": "大纲",
        "tasks": ["outline"],
        "canonical_task": "outline",
        "structure_role": "outline",
        "flow": "planning_archive",
        "node_flow": ["request_analyze", "draft_assemble", "provider_route", "generate", "model_review", "draft_finalize", "archive"],
        "material_sections": ["outline", "base_setting", "character", "worldview", "plot", "chapter_summary", "project_wiki", "writing_techniques"],
        "acceptance_signals": ["全书结构", "章节", "主线", "冲突", "转折", "钩子", "伏笔", "回收"],
    },
    "plot": {
        "id": "plot",
        "label": "情节",
        "tasks": ["beat_sheet", "plot"],
        "canonical_task": "beat_sheet",
        "structure_role": "plot",
        "flow": "planning_light",
        "node_flow": ["request_analyze", "draft_assemble", "provider_route", "generate", "model_review", "draft_finalize"],
        "material_sections": ["plot", "outline", "character", "worldview", "chapter_summary", "project_wiki", "writing_techniques"],
        "acceptance_signals": ["主线", "场景", "行动", "阻力", "变化", "压力源", "释放", "余味"],
    },
    "prose": {
        "id": "prose",
        "label": "正文",
        "tasks": ["prose", "expansion", "fix"],
        "canonical_task": "prose",
        "structure_role": "chapter_body",
        "flow": "full_generation",
        "node_flow": [
            "request_analyze", "draft_assemble", "provider_route", "generate",
            "pre_review", "model_review", "draft_finalize", "archive",
        ],
        "material_sections": ["outline", "character", "worldview", "plot", "chapter_summary", "style_guide", "project_wiki", "writing_techniques", "reference_novels"],
        "acceptance_signals": ["场景", "动作", "对白", "冲突", "连续性", "材料来源", "章节目标"],
    },
}

_TASK_TO_STAGE: dict[str, str] = {
    task: stage_id
    for stage_id, profile in NOVEL_STAGE_PROFILES.items()
    for task in profile["tasks"]
}

NOVEL_PLANNING_TASKS = frozenset(
    task
    for stage_id, profile in NOVEL_STAGE_PROFILES.items()
    if profile["flow"] in {"planning_light", "planning_archive"}
    for task in profile["tasks"]
)


def normalized_task(task: str | None) -> str:
    return str(task or "").strip().lower()


def novel_stage_for_task(task: str | None) -> str:
    return _TASK_TO_STAGE.get(normalized_task(task), "")


def novel_stage_profile(task: str | None) -> dict[str, Any]:
    stage_id = novel_stage_for_task(task)
    if not stage_id:
        return {}
    profile = dict(NOVEL_STAGE_PROFILES[stage_id])
    profile["tasks"] = list(profile.get("tasks") or [])
    profile["node_flow"] = list(profile.get("node_flow") or [])
    profile["material_sections"] = list(profile.get("material_sections") or [])
    profile["acceptance_signals"] = list(profile.get("acceptance_signals") or [])
    return profile


def stage_options_for_prompt() -> list[dict[str, Any]]:
    return [
        {
            "creative_stage": profile["id"],
            "label": profile["label"],
            "tasks": profile["tasks"],
            "canonical_task": profile["canonical_task"],
            "target_role": profile["structure_role"],
            "flow": profile["flow"],
        }
        for profile in NOVEL_STAGE_PROFILES.values()
    ]


def normalize_novel_task(task: str | None, creative_stage: str | None = None) -> str:
    task_key = normalized_task(task)
    if task_key in {"prose", "expansion", "fix"}:
        return task_key
    if task_key in _TASK_TO_STAGE:
        return str(NOVEL_STAGE_PROFILES[_TASK_TO_STAGE[task_key]]["canonical_task"])
    stage_key = normalized_task(creative_stage)
    profile = NOVEL_STAGE_PROFILES.get(stage_key)
    if profile:
        return str(profile["canonical_task"])
    return task_key


def is_novel_planning_task(project_kind: str | None, task: str | None) -> bool:
    return project_kind == STRONG_NOVEL_KIND and normalized_task(task) in NOVEL_PLANNING_TASKS


def enrich_novel_stage_analysis(
    analysis: dict[str, Any],
    *,
    project_kind: str | None,
    project_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if project_kind != STRONG_NOVEL_KIND:
        return analysis
    enriched = dict(analysis or {})
    task = normalize_novel_task(enriched.get("task"), enriched.get("creative_stage"))
    if task:
        enriched["task"] = task
    profile = novel_stage_profile(task)
    if not profile:
        return enriched
    enriched["creative_stage"] = profile["id"]
    enriched["creative_stage_label"] = profile["label"]
    enriched["stage_profile"] = profile
    enriched["target_role"] = profile["structure_role"]
    enriched["node_flow"] = profile["node_flow"]
    enriched["material_sections"] = profile["material_sections"]
    enriched["acceptance_signals"] = profile["acceptance_signals"]
    enriched["flow_complexity"] = profile["flow"]
    conflict = _stage_conflict(profile["id"], project_progress or {})
    if conflict:
        enriched["stage_conflict"] = conflict
    return enriched


def _stage_conflict(stage_id: str, progress: dict[str, Any]) -> dict[str, Any]:
    current = str(progress.get("current_stage_key") or "").strip()
    if not current or current == stage_id:
        return {}
    order = list(NOVEL_STAGE_PROFILES.keys())
    if current not in order or stage_id not in order:
        return {}
    current_idx = order.index(current)
    target_idx = order.index(stage_id)
    if target_idx < current_idx:
        relation = "backfill"
        message = "用户目标阶段早于当前项目阶段，按用户显式意图回补前置材料。"
    else:
        relation = "jump_ahead"
        message = "用户目标阶段晚于当前项目阶段，流程将继续执行，但需要检查前置材料是否足够。"
    return {
        "current_stage": current,
        "current_stage_label": progress.get("current_stage") or "",
        "target_stage": stage_id,
        "relation": relation,
        "message": message,
    }
