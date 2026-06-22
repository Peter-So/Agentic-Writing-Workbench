from __future__ import annotations

import json
import re
from typing import Any

from app.config import load_runtime_config
from app.llm_client import create_llm, resolve_text_model


SYSTEM_PROMPT = (
    "你是写作系统的请求理解与流程路由器。"
    "你只分析用户真实需求，不创作正文。"
    "必须输出严格 JSON，不要输出 markdown。"
)


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def _clean_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _clean_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        parsed = _clean_int(item)
        if parsed and parsed not in out:
            out.append(parsed)
    return out[:8]


def _clean_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text[:80])
    return out[:10]


def analyze_writing_request(
    *,
    message: str,
    mode: str,
    task: str,
    chapter: int | None,
    project_kind: str | None = None,
    novel_id: str | None = None,
    project_progress: dict[str, Any] | None = None,
    model_key: str | None = None,
) -> dict[str, Any]:
    """Use the configured LLM to understand the user request before graph routing.

    The result is intentionally small and structured so graph nodes can route by
    data instead of keyword checks.
    """
    cfg = load_runtime_config()
    selected_model = resolve_text_model(cfg, "chat", model_key)
    llm = create_llm(cfg, selected_model, temperature=0, max_tokens=900, timeout=45, max_retries=1)
    structure_context: dict[str, Any] = {}
    try:
        from app.project_structure import structure_prompt_context

        structure_context = structure_prompt_context(novel_id, project_kind)
    except Exception:
        structure_context = {}
    target_paths = structure_context.get("target_paths") or []
    target_labels = structure_context.get("target_labels") or []
    task_roles = structure_context.get("task_roles") or {}
    schema = {
        "intent": "draft | revise | review | assemble | search | build_index",
        "task": "logline | setting | world | outline | character | beat_sheet | prose | expansion | fix | screenplay | storyboard | visual_prompt | image | generic",
        "deliverable": "generation | audit_report | material_bundle | search_results | rewrite | answer",
        "target_chapter": "positive integer or null",
        "context_chapters": "array of positive integers needed as evidence/context",
        "flow_entry": "draft_entry | review | assemble | search | build_index",
        "generator_instruction": "one concise Chinese instruction for the generation node",
        "answer_style": "prose | structured_report | list | screenplay | storyboard",
        "affected_materials": ["project structure roles, e.g. character | screenplay | draft"],
        "affected_files": ["paths or labels from project_structure, not hard-coded"],
        "involved_characters": ["character names explicitly involved"],
        "plot_points": ["plot/event points explicitly involved"],
        "target_sections": ["section titles, scene names, or exact phrases that locate the affected text"],
        "impact_reason": "short Chinese explanation of why multiple files may be affected",
        "confidence": "0.0-1.0",
        "reason": "short Chinese reason",
    }
    progress = project_progress or {}
    progress_text = json.dumps(progress, ensure_ascii=False, indent=2) if progress else "（未提供）"
    structure_text = json.dumps({
        "project_kind": structure_context.get("project_kind") or project_kind or "",
        "documents": structure_context.get("documents") or [],
        "task_roles": task_roles,
    }, ensure_ascii=False, indent=2) if structure_context else "（未加载项目结构 Wiki）"
    kind_rules = {
        "novel_strong": (
            "小说项目：基础设定/题材/主题对应 setting/logline；世界观对应 world；人物设定对应 character；"
            "情节/节拍/剧情推进对应 beat_sheet；全书/分卷/章节大纲对应 outline；章节正文对应 prose。"
        ),
        "short_film": (
            "电影脚本项目：概念/logline 对应 logline；角色对应 character；节拍/结构对应 beat_sheet；"
            "剧本正文对应 screenplay；分镜对应 shot_list/storyboard；视觉提示词/生图对应 visual_prompt/image。"
        ),
        "generic": (
            "随想项目：零散记录对应 materials/generic；可发展想法和结构对应 outline；扩写成文对应 draft/prose；"
            "参考资料对应 references。"
        ),
    }.get(structure_context.get("project_kind") or project_kind or "", "按项目结构 Wiki 中的 role/path 选择任务和受影响文件。")
    prompt = (
        "请解析本轮用户请求，并决定 LangGraph 应接入的流程节点。\n"
        "不要根据 UI 默认章节盲目判断，要以用户文本为准；若用户文本没有明确章节，保留传入章节。\n"
        "必须参考项目状态：当用户说继续、下一章、当前进度、生成本章等模糊表达时，优先使用项目状态里的 current_chapter/current_stage/status_label 推断目标。\n"
        "若用户要求检查/评估/分析已有材料并需要系统输出报告，deliverable 应为 audit_report，answer_style 应为 structured_report，flow_entry 必须为 draft_entry。\n"
        "若用户要求写作/生成/扩写，deliverable 应为 generation。\n"
        f"{kind_rules}\n"
        "affected_materials 必须优先使用项目结构 Wiki 中的 role；affected_files 必须优先使用项目结构 Wiki 中的 path 或 label。\n"
        "用户说调整某个人物、某段剧情、某个设定、某个节拍、某段剧本、某个分镜、某条随想时，不要只选单文件；affected_materials/affected_files 必须列出可能被牵连的结构材料。\n"
        "involved_characters 填本次明确涉及的人物名；plot_points 填涉及的剧情/事件；target_sections 填能定位文件位置的标题、段落关键词或场景名。\n"
        "context_chapters 填需要一起读取的章节材料，例如用户要求根据前两章检查第三章，应包含 1,2,3。\n\n"
        f"输出 JSON schema 示例：{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"当前项目：{novel_id or ''}\n"
        f"项目类型：{project_kind or ''}\n"
        f"项目结构 Wiki：{structure_text}\n"
        f"可选 affected_files 路径：{json.dumps(target_paths, ensure_ascii=False)}\n"
        f"可选 affected_files 标签：{json.dumps(target_labels, ensure_ascii=False)}\n"
        f"项目状态：{progress_text}\n"
        f"UI mode：{mode}\n"
        f"UI task：{task}\n"
        f"UI chapter：{chapter}\n"
        f"用户请求：{message}\n"
    )
    resp = llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "human", "content": prompt},
    ])
    parsed = _extract_json(getattr(resp, "content", "") or "")
    target_chapter = _clean_int(parsed.get("target_chapter")) or chapter
    context_chapters = _clean_int_list(parsed.get("context_chapters"))
    if target_chapter and target_chapter not in context_chapters:
        context_chapters.append(target_chapter)
        context_chapters = sorted(set(context_chapters))
    return {
        "ok": True,
        "source": "llm",
        "model_key": selected_model,
        "project_progress": progress,
        "intent": str(parsed.get("intent") or mode or "draft"),
        "task": str(parsed.get("task") or task or "prose"),
        "deliverable": str(parsed.get("deliverable") or "generation"),
        "target_chapter": target_chapter,
        "context_chapters": context_chapters,
        "flow_entry": str(parsed.get("flow_entry") or "draft_entry"),
        "generator_instruction": str(parsed.get("generator_instruction") or "").strip(),
        "answer_style": str(parsed.get("answer_style") or "").strip(),
        "affected_materials": _clean_str_list(parsed.get("affected_materials")),
        "affected_files": _clean_str_list(parsed.get("affected_files")),
        "involved_characters": _clean_str_list(parsed.get("involved_characters")),
        "plot_points": _clean_str_list(parsed.get("plot_points")),
        "target_sections": _clean_str_list(parsed.get("target_sections")),
        "impact_reason": str(parsed.get("impact_reason") or "").strip(),
        "confidence": parsed.get("confidence"),
        "reason": str(parsed.get("reason") or "").strip(),
    }


def fallback_request_analysis(*, message: str, mode: str, task: str, chapter: int | None,
                              error: str = "") -> dict[str, Any]:
    """Non-routing fallback: preserve user/UI state when the LLM analyzer fails."""
    return {
        "ok": False,
        "source": "fallback",
        "intent": mode or "draft",
        "task": task or "prose",
        "deliverable": "generation",
        "target_chapter": chapter,
        "context_chapters": [chapter] if chapter else [],
        "flow_entry": "draft_entry",
        "generator_instruction": "",
        "answer_style": "",
        "affected_materials": [],
        "affected_files": [],
        "involved_characters": [],
        "plot_points": [],
        "target_sections": [],
        "impact_reason": "",
        "confidence": 0,
        "reason": "LLM 请求理解失败，保留原始 UI 参数。",
        "error": error,
    }
