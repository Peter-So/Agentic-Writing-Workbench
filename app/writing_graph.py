from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from app import writing_tools
from app.config import ROOT
from app.writing_sop import sop_for_task
from app.writing_generate import generate_prose
from app.writing_memory import get_checkpointer
from app.writing_model_review import model_review_cross, review_feedback_text
from app.writing_need_audit import audit_need
from app.writing_review_strategy import decide_review_strategy, deterministic_review
from app.writing_task_profiles import apply_stage_material_profile, is_novel_planning_task


# 回环上限：审查不过最多回补材料重组 2 次（规范要求，防无限烧钱）。
MAX_ITERATIONS = 2
# LangGraph 递归保险：正常创作流远低于该值，未来新增边时用于兜底防无限循环。
GRAPH_RECURSION_LIMIT = 30
# M3：对话消息超过此 token 阈值时，自动把旧消息压成前情摘要，保留最近 KEEP_RECENT 条。
COMPRESS_TOKEN_THRESHOLD = 4000
KEEP_RECENT_MESSAGES = 6


def _emit_graph_stage(stage: str, status: str = "running", **details: Any) -> None:
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:
        writer = None
    if not writer:
        return
    try:
        writer({"type": "stage", "stage": stage, "status": status, **details})
    except Exception:
        pass


GRAPH_NODE_DESCRIPTIONS: dict[str, str] = {
    "__start__": "LangGraph 入口。",
    "request_analyze": "使用 LLM 理解用户提问、项目类型、章节、目标文件与流程入口；保存 pending intent，并触发项目 Wiki 路由索引。",
    "novel_stage_route": "小说项目专用：把 LLM 意图规范化为 creative_stage、task、flow_complexity 和 node_flow，区分规划精简流与正文完整流。",
    "project_wiki_route": "LLM 意图分析后，把本轮 task、阶段、节点流、相关文件、正文行号和 pending intent 写入项目 Wiki 路由索引。",
    "compress_memory": "当上下文过长时压缩旧消息，保留可恢复的短期任务记忆。",
    "prepare_project": "检查项目类型与结构，必要时初始化项目规范目录。",
    "route_intent": "根据意图分析结果进入搜索、材料组装、审查、索引或创作分支。",
    "build_index": "重建参考资料与语义检索索引。",
    "review": "执行章节或材料审查。",
    "assemble": "只组装材料，不生成正文。",
    "search": "检索参考资料、五维库或项目相关材料。",
    "draft_entry": "进入创作或改写分支。",
    "need_audit": "审计需求复杂度、材料依赖和流程风险。",
    "material_profile": "按阶段 profile 切换材料范围：前期规划只取结构材料、Wiki 和技法；正文阶段补充大纲、前情、人物、风格、参考小说和连续性记忆。",
    "draft_assemble": "按项目 Wiki、章节、人物、前情、技法与限制精确组装材料；生成材料索引，精修任务会携带正文文件行号与待改片段。",
    "creative_state": "读取状态卡、短片状态卡和伏笔账本；缺失时降级，不阻塞创作。",
    "methodology_context": "从公共创作方法论知识库匹配阶段方法、Prompt 契约、研究和风格法则。",
    "creative_enhancements": "生成参考拆解卡、章节/场次功能、读者体验、卖点包装、研究、自检和去 AI 味检查。",
    "generate": "调用创作模型生成或根据审查反馈重新生成。",
    "pre_review": "规则预审，发现硬性问题时进入回环修复；小说前期规划任务会轻量跳过，正文/扩写/修复执行完整预审。",
    "model_review": "审查模型评分与反馈，必要时触发重新生成。",
    "draft_finalize": "清洗定稿内容，准备用户确认与后续归档。",
    "user_confirm": "用户确认采纳、拒绝或改写后采纳；确认后进入归档或项目产物保存闭环。",
    "archive_write": "把确认稿写入章节正文、大纲、剧本、草稿或类型产物，并清理/归档 pending intent。",
    "project_wiki_archive": "归档完成后，把归档文件、摘要、路由来源、相关文件和内容预览写入项目 Wiki 归档摘要。",
    "idea_settle": "随想项目在用户确认后沉淀为灵感、笔记或项目 Wiki 条目。",
    "visual_prompt": "电影脚本项目基于剧本、节拍与影像风格生成分镜/生图提示词。",
    "image_plan": "补齐镜头比例、分辨率、角色一致性和画面连续性参数。",
    "image_generate": "调用生图模型生成关键帧、起承转合帧或角色参考图。",
    "storyboard_archive": "归档分镜、生图结果与可复用影像经验。",
    "__end__": "LangGraph 本轮流程结束。",
}


GRAPH_NODE_GROUPS: dict[str, str] = {
    "__start__": "系统边界",
    "__end__": "系统边界",
    "request_analyze": "入口理解",
    "novel_stage_route": "入口理解",
    "project_wiki_route": "入口理解",
    "compress_memory": "入口理解",
    "prepare_project": "入口理解",
    "route_intent": "入口理解",
    "build_index": "路由分支",
    "review": "路由分支",
    "assemble": "路由分支",
    "search": "路由分支",
    "draft_entry": "创作主线",
    "need_audit": "创作主线",
    "draft_assemble": "创作主线",
    "creative_state": "创作主线",
    "methodology_context": "创作主线",
    "creative_enhancements": "创作主线",
    "material_profile": "创作主线",
    "generate": "审查回环",
    "pre_review": "审查回环",
    "model_review": "审查回环",
    "draft_finalize": "定稿确认",
    "user_confirm": "定稿确认",
    "archive_write": "定稿确认",
    "project_wiki_archive": "定稿确认",
    "idea_settle": "定稿确认",
    "visual_prompt": "影像生图",
    "image_plan": "影像生图",
    "image_generate": "影像生图",
    "storyboard_archive": "影像生图",
}


PROJECT_KIND_GRAPH_NOTES: dict[str, list[str]] = {
    "novel_strong": [
        "小说项目先由 LLM 意图分析输出 creative_stage/task/flow_complexity/node_flow，再按阶段 profile 切换材料组装。",
        "概念、基础设定、世界观、人物、情节属于 planning_light 精简流：不写正文，材料聚焦结构文件、项目 Wiki 和技法知识库，规则预审轻量跳过。",
        "大纲属于 planning_archive 精简归档流：聚焦大纲、人物、世界观、情节和章节摘要，用户确认后进入显式归档。",
        "正文、扩写、修复属于 full_generation 完整正文流：补充大纲、前情、人物、风格、参考小说、连续性记忆；fix/expansion 会携带正文文件行号和待改片段。",
        "方法论、状态卡、参考拆解、章节功能、读者体验、研究节点、自检和去 AI 味检查会作为增强卡进入材料区，并由阶段 profile 裁剪。",
        "章节正文、大纲、人物、设定等写回动作在用户确认后由归档流程执行，不在 LangGraph 内静默覆盖文件。",
    ],
    "short_film": [
        "电影脚本项目复用主创作链路，剧本、节拍、分镜提示词和生图由项目类型能力补充。",
        "短片流程会显示场次功能、观众体验、事实考据、自检和可拍性/去 AI 味检查。",
        "规则预审对短片类型通常降级为轻量检查，重点由需求审计、模型审查和用户确认兜底。",
    ],
    "generic": [
        "随想项目复用意图分析、材料整理、生成与确认链路，适合灵感、草稿和参考材料整理。",
        "规则预审通常轻量化，避免随想类任务被小说专用规则过度约束。",
    ],
}


GRAPH_CANVAS = {"width": 1420, "height": 1700}
GRAPH_BASE_LAYOUT: dict[str, tuple[int, int]] = {
    "__start__": (660, 44),
    "request_analyze": (660, 140),
    "novel_stage_route": (360, 236),
    "project_wiki_route": (980, 140),
    "compress_memory": (660, 236),
    "prepare_project": (660, 332),
    "route_intent": (660, 428),
    "build_index": (160, 548),
    "review": (360, 548),
    "assemble": (560, 548),
    "search": (760, 548),
    "draft_entry": (1000, 548),
    "need_audit": (1000, 654),
    "material_profile": (760, 760),
    "draft_assemble": (1000, 760),
    "creative_state": (760, 866),
    "methodology_context": (1000, 866),
    "creative_enhancements": (1200, 866),
    "generate": (1000, 972),
    "pre_review": (1000, 1078),
    "model_review": (1000, 1194),
    "draft_finalize": (1000, 1416),
    "user_confirm": (760, 1510),
    "archive_write": (1000, 1588),
    "project_wiki_archive": (1200, 1588),
    "__end__": (660, 1648),
}
GRAPH_BASE_BANDS = [
    ["入口理解", 92, 476],
    ["路由分支", 508, 592],
    ["创作主线", 620, 1010],
    ["审查回环", 940, 1340],
    ["定稿确认", 1378, 1688],
]

COMMON_GRAPH_NODE_IDS = {
    "__start__", "request_analyze", "compress_memory", "prepare_project", "route_intent", "__end__",
}

PROJECT_WIKI_FLOW_NODES = ["project_wiki_route", "user_confirm", "archive_write", "project_wiki_archive"]
PROJECT_WIKI_FLOW_EDGES = [
    {"source": "request_analyze", "target": "project_wiki_route", "label": "写路由索引", "conditional": False},
    {"source": "project_wiki_route", "target": "compress_memory", "label": "", "conditional": False},
    {"source": "draft_finalize", "target": "user_confirm", "label": "等待采纳", "conditional": True},
    {"source": "user_confirm", "target": "archive_write", "label": "确认归档", "conditional": True},
    {"source": "archive_write", "target": "project_wiki_archive", "label": "写归档摘要", "conditional": False},
    {"source": "project_wiki_archive", "target": "__end__", "label": "", "conditional": False},
]

CREATIVE_ENHANCEMENT_FLOW_NODES = ["creative_state", "methodology_context", "creative_enhancements"]
CREATIVE_ENHANCEMENT_FLOW_EDGES = [
    {"source": "draft_assemble", "target": "creative_state", "label": "读状态", "conditional": False},
    {"source": "creative_state", "target": "methodology_context", "label": "匹配方法", "conditional": False},
    {"source": "methodology_context", "target": "creative_enhancements", "label": "生成增强卡", "conditional": False},
    {"source": "creative_enhancements", "target": "material_profile", "label": "交给阶段裁剪", "conditional": False},
    {"source": "material_profile", "target": "generate", "label": "进入生成", "conditional": False},
]

NOVEL_STAGE_FLOW_NODES = ["novel_stage_route", "material_profile"]
NOVEL_STAGE_FLOW_EDGES = [
    {"source": "request_analyze", "target": "novel_stage_route", "label": "阶段标识", "conditional": False},
    {"source": "novel_stage_route", "target": "draft_assemble", "label": "阶段材料组装", "conditional": False},
]

PROJECT_GRAPH_PROFILES: dict[str, dict[str, Any]] = {
    "novel_strong": {
        "visible": None,
        "extra_nodes": [*PROJECT_WIKI_FLOW_NODES, *NOVEL_STAGE_FLOW_NODES, *CREATIVE_ENHANCEMENT_FLOW_NODES],
        "extra_edges": [*PROJECT_WIKI_FLOW_EDGES, *NOVEL_STAGE_FLOW_EDGES, *CREATIVE_ENHANCEMENT_FLOW_EDGES],
        "layout": {},
        "group_bands": GRAPH_BASE_BANDS,
    },
    "short_film": {
        "visible": COMMON_GRAPH_NODE_IDS | {
            "assemble", "search", "draft_entry", "need_audit", "draft_assemble",
            "generate", "pre_review", "model_review", "draft_finalize",
        },
        "extra_nodes": [*PROJECT_WIKI_FLOW_NODES, *CREATIVE_ENHANCEMENT_FLOW_NODES, "material_profile", "visual_prompt", "image_plan", "image_generate", "storyboard_archive"],
        "extra_edges": [
            *PROJECT_WIKI_FLOW_EDGES,
            *CREATIVE_ENHANCEMENT_FLOW_EDGES,
            {"source": "draft_finalize", "target": "visual_prompt", "label": "需分镜/生图", "conditional": True},
            {"source": "visual_prompt", "target": "image_plan", "label": "", "conditional": False},
            {"source": "image_plan", "target": "image_generate", "label": "", "conditional": False},
            {"source": "image_generate", "target": "storyboard_archive", "label": "", "conditional": False},
            {"source": "storyboard_archive", "target": "__end__", "label": "", "conditional": False},
        ],
        "layout": {
            "visual_prompt": (560, 1320),
            "image_plan": (560, 1426),
            "image_generate": (760, 1488),
            "storyboard_archive": (980, 1532),
            "user_confirm": (760, 1364),
            "archive_write": (1010, 1430),
            "project_wiki_archive": (1210, 1510),
            "__end__": (660, 1648),
        },
        "group_bands": [
            ["入口理解", 92, 476],
            ["剧本材料", 508, 1010],
            ["审查回环", 940, 1340],
            ["影像生图", 1278, 1570],
            ["定稿确认", 1320, 1688],
        ],
    },
    "generic": {
        "visible": COMMON_GRAPH_NODE_IDS | {
            "assemble", "search", "draft_entry", "need_audit", "draft_assemble",
            "generate", "model_review", "draft_finalize",
        },
        "extra_nodes": [*PROJECT_WIKI_FLOW_NODES, "idea_settle"],
        "extra_edges": [
            *PROJECT_WIKI_FLOW_EDGES,
            {"source": "generate", "target": "model_review", "label": "轻量审查", "conditional": False},
            {"source": "draft_finalize", "target": "idea_settle", "label": "采纳后沉淀", "conditional": True},
            {"source": "idea_settle", "target": "__end__", "label": "", "conditional": False},
        ],
        "layout": {
            "search": (360, 548),
            "assemble": (580, 548),
            "draft_entry": (900, 548),
            "need_audit": (900, 654),
            "draft_assemble": (900, 760),
            "generate": (900, 972),
            "model_review": (900, 1194),
            "draft_finalize": (900, 1300),
            "idea_settle": (900, 1416),
            "user_confirm": (660, 1416),
            "archive_write": (900, 1510),
            "project_wiki_archive": (1120, 1510),
            "__end__": (660, 1648),
        },
        "group_bands": [
            ["入口理解", 92, 476],
            ["灵感材料", 508, 592],
            ["随想成稿", 620, 1242],
            ["确认沉淀", 1268, 1688],
        ],
    },
}


def _visual_mermaid(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    """Build a Mermaid view that matches the filtered Web visualization."""
    node_by_id = {node["id"]: node for node in nodes}
    lines = ["flowchart TD"]
    for node in nodes:
        label = str(node.get("label") or node["id"]).replace('"', "'")
        lines.append(f'  {node["id"]}["{label}"]')
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source not in node_by_id or target not in node_by_id:
            continue
        label = str(edge.get("label") or "").strip().replace('"', "'")
        if label:
            lines.append(f'  {source} -- "{label}" --> {target}')
        else:
            lines.append(f"  {source} --> {target}")
    return "\n".join(lines)


def _outline_file_for(novel_id: str | None) -> Path | None:
    try:
        from app.project_structure import find_related_structure_file, resolve_structure_target

        _role, routed = resolve_structure_target(novel_id, "outline", create_missing=False)
        if routed and routed.is_file():
            return routed
        matched = find_related_structure_file(novel_id, "outline")
        if matched and matched[1].is_file():
            return matched[1]
    except Exception:
        pass
    return None


def _extract_markdown_chapter(content: str, chapter: int) -> str:
    cn_nums = "一二三四五六七八九十"
    cn = cn_nums[chapter - 1] if 1 <= chapter <= len(cn_nums) else str(chapter)
    lines = content.splitlines()
    patterns = [
        rf"^#{{1,4}}\s*第\s*{chapter}\s*章\b",
        rf"^#{{1,4}}\s*第\s*{cn}\s*章\b",
        rf"^#{{1,4}}\s*Ch\s*{chapter}\b",
        rf"^#{{1,4}}\s*Chapter\s*{chapter}\b",
    ]
    start_idx = -1
    start_level = 0
    for i, line in enumerate(lines):
        if any(re.match(pattern, line, re.IGNORECASE) for pattern in patterns):
            start_idx = i
            start_level = len(line) - len(line.lstrip("#"))
            break
    if start_idx < 0:
        return ""
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        if re.match(r"^#{1,4}\s", line):
            level = len(line) - len(line.lstrip("#"))
            if level <= start_level:
                end_idx = i
                break
    return "\n".join(lines[start_idx:end_idx]).strip()


def _load_outline_context(novel_id: str | None, chapters: list[int]) -> str:
    chapters = [chapter for chapter in chapters if isinstance(chapter, int) and chapter > 0]
    if not chapters:
        return ""
    path = _outline_file_for(novel_id)
    if not path:
        return ""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    blocks = []
    for chapter in chapters:
        block = _extract_markdown_chapter(content, chapter)
        if block:
            blocks.append(f"[第{chapter}章]\n{block[:5000]}")
    if not blocks:
        return ""
    rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
    return f"来源：{rel}\n\n" + "\n\n---\n\n".join(blocks)


def _flow_debug(message: str) -> None:
    """Append compact writing-flow diagnostics without storing prompt bodies."""
    try:
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "writing_flow.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except Exception:
        pass


class WritingState(TypedDict, total=False):
    """LangGraph 写作工作流状态。"""
    user_message: str
    mode: str
    intent: str
    chapter: int | None
    task: str
    dimension: str | None
    top_k: int
    # 生成/审查回环相关
    track: str
    novel_id: str
    project_kind: str
    project_init: dict
    request_analysis: dict
    pending_intent: dict
    need_audit: dict
    bundle: dict
    workflow_sop: dict
    invocation_id: str
    model_preferences: dict[str, str]
    merge_info: dict
    draft: str
    pre_review: dict
    model_review: dict
    review_strategy: dict
    iterations: int
    actions: list[str]
    data: dict[str, Any]
    error: str | None
    # M1 短期记忆：多轮消息（add_messages reducer 自动累积/合并）
    messages: Annotated[list, add_messages]


def _resolve_intent(message: str, mode: str) -> str:
    """与 WritingAgent._resolve_intent 等价的意图判定，并新增 draft/revise。"""
    if mode and mode != "auto":
        return mode
    text = (message or "").lower()
    if "build-index" in text or "构建索引" in text or "重建索引" in text:
        return "build_index"
    if "审查" in text or "检查章节" in text or "review" in text:
        return "review"
    if "修订" in text or "修复" in text or "revise" in text:
        return "revise"
    if "写正文" in text or "生成正文" in text or "草稿" in text or "draft" in text or "写第" in text:
        return "draft"
    if "材料" in text or "组装" in text or "assemble" in text:
        return "assemble"
    return "search"


def route_intent(state: WritingState) -> WritingState:
    analysis = state.get("request_analysis") or {}
    flow_entry = analysis.get("flow_entry") if analysis.get("ok") else ""
    if analysis.get("deliverable") == "audit_report":
        intent = "draft"
    elif flow_entry in {"build_index", "review", "assemble", "search"}:
        intent = flow_entry
    elif flow_entry == "draft_entry":
        intent = "revise" if state.get("mode") == "revise" else "draft"
    elif analysis.get("intent") in {"build_index", "review", "assemble", "search", "draft", "revise"}:
        intent = analysis["intent"]
    else:
        intent = _resolve_intent(state.get("user_message", ""), state.get("mode", "auto"))
    actions = list(state.get("actions") or [])
    actions.append(f"route_intent({intent})")
    return {"intent": intent, "actions": actions, "iterations": 0}


def node_prepare_project(state: WritingState) -> WritingState:
    """Detect project type and initialize empty projects with a fitting scaffold."""
    try:
        from app.project_kinds import ensure_project_initialized, project_kind

        init = ensure_project_initialized(state.get("novel_id"), state.get("user_message", ""))
        actions = list(state.get("actions") or [])
        kind = init.get("kind") or project_kind(state.get("novel_id"))
        if init.get("created"):
            actions.append(f"init_project({kind})")
        else:
            actions.append(f"project_kind({kind})")
        return {"project_kind": kind, "project_init": init, "actions": actions}
    except Exception as exc:
        return {"project_kind": "generic", "project_init": {"ok": False, "error": str(exc)}}


def node_request_analyze(state: WritingState) -> WritingState:
    """LLM-based request understanding before choosing the concrete flow."""
    message = state.get("user_message", "") or ""
    mode = state.get("mode", "auto")
    task = state.get("task", "prose")
    chapter = state.get("chapter")
    kind = state.get("project_kind")
    if not kind:
        try:
            from app.project_kinds import project_kind
            kind = project_kind(state.get("novel_id"))
        except Exception:
            kind = ""
    progress: dict[str, Any] = {}
    try:
        from app.writing_tools import project_progress
        progress = project_progress(state.get("novel_id"))
    except Exception:
        progress = {}
    actions = list(state.get("actions") or [])
    recovered_intent: dict[str, Any] | None = None
    try:
        from app.pending_intent_memory import recover_pending_intent_by_message

        recovered_intent = recover_pending_intent_by_message(
            novel_id=state.get("novel_id"),
            track=state.get("track", "create"),
            message=message,
        )
    except Exception as exc:
        actions.append(f"pending_intent_reuse_failed({type(exc).__name__})")
    if recovered_intent:
        analysis = dict(recovered_intent.get("analysis") or {})
        original_source = analysis.get("source")
        analysis.setdefault("ok", True)
        analysis["source"] = "pending_intent_cache"
        analysis["reused_from"] = recovered_intent.get("id")
        analysis["reused_memory_source"] = recovered_intent.get("memory_source")
        analysis["message_match_score"] = recovered_intent.get("message_match_score")
        analysis["message_match_kind"] = recovered_intent.get("message_match_kind")
        if original_source and original_source != "pending_intent_cache":
            analysis["original_source"] = original_source
        actions.append(
            "pending_intent_reused("
            f"{recovered_intent.get('memory_source')},{recovered_intent.get('message_match_kind')})"
        )
    else:
        try:
            from app.writing_request_analysis import analyze_writing_request

            analysis = analyze_writing_request(
                message=message,
                mode=mode,
                task=task,
                chapter=chapter,
                project_kind=kind,
                novel_id=state.get("novel_id"),
                project_progress=progress,
                model_key=(state.get("model_preferences") or {}).get("chat"),
            )
        except Exception as exc:
            if "模型" in str(exc) or "model" in str(exc).lower():
                raise
            from app.writing_request_analysis import fallback_request_analysis

            analysis = fallback_request_analysis(
                message=message,
                mode=mode,
                task=task,
                chapter=chapter,
                error=f"{type(exc).__name__}: {exc}",
                project_kind=kind,
                project_progress=progress,
                novel_id=state.get("novel_id"),
            )
    try:
        from app.project_kinds import STRONG_NOVEL_KIND
        from app.prose_locator import is_prose_refinement_intent, locate_prose_targets

        if kind == STRONG_NOVEL_KIND and not analysis.get("prose_locations") and is_prose_refinement_intent(analysis, analysis.get("task") or task):
            prose_locations = locate_prose_targets(
                novel_id=state.get("novel_id"),
                chapter=analysis.get("target_chapter") or chapter,
                analysis=analysis,
                message=message,
            )
            if prose_locations:
                analysis = dict(analysis)
                analysis["prose_locations"] = prose_locations
                affected = list(analysis.get("affected_files") or [])
                for loc in prose_locations:
                    path = loc.get("path")
                    if path and path not in affected:
                        affected.append(path)
                analysis["affected_files"] = affected[:10]
    except Exception:
        pass
    actions.append(
        "request_analyze("
        f"{analysis.get('source')},{analysis.get('deliverable')},{analysis.get('target_chapter')})"
    )
    pending_intent = {}
    try:
        from app.pending_intent_memory import save_pending_intent

        pending_intent = save_pending_intent(
            novel_id=state.get("novel_id"),
            track=state.get("track", "create"),
            invocation_id=state.get("invocation_id", ""),
            message=message,
            analysis=analysis,
            task=task,
            chapter=chapter,
            project_kind=kind,
        )
        actions.append("pending_intent_saved(short)")
    except Exception as exc:
        pending_intent = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        actions.append("pending_intent_save_failed")
    try:
        from app.project_wiki_events import record_request_route_index

        route_wiki = record_request_route_index(
            novel_id=state.get("novel_id"),
            invocation_id=state.get("invocation_id", ""),
            track=state.get("track", "create"),
            message=message,
            analysis=analysis,
            pending_intent=pending_intent,
            project_kind=kind,
        )
        if route_wiki.get("ok"):
            actions.append("project_wiki_route_index")
    except Exception as exc:
        actions.append(f"project_wiki_route_failed({type(exc).__name__})")
    updates: WritingState = {"request_analysis": analysis, "actions": actions}
    if pending_intent:
        updates["pending_intent"] = pending_intent
    if kind:
        updates["project_kind"] = kind
    target_chapter = analysis.get("target_chapter")
    if isinstance(target_chapter, int) and target_chapter > 0:
        updates["chapter"] = target_chapter
    suggested_task = analysis.get("task")
    if suggested_task and suggested_task != "generic":
        updates["task"] = suggested_task
    return updates


def node_need_audit(state: WritingState) -> WritingState:
    audit = audit_need(
        message=state.get("user_message", ""),
        project_kind=state.get("project_kind"),
        task=state.get("task", "prose"),
        chapter=state.get("chapter"),
    )
    actions = list(state.get("actions") or [])
    actions.append(f"need_audit({audit.get('level')},{audit.get('deliverable')})")
    return {"need_audit": audit, "actions": actions}


def node_compress_memory(state: WritingState) -> WritingState:
    """M3 自动节点：对话消息超 token 阈值时，把旧消息压成前情摘要并替换，防止上下文撑爆。

    机制：保留最近 KEEP_RECENT_MESSAGES 条，更早的用便宜模型压成一段摘要 SystemMessage，
    用 RemoveMessage 删除被压缩的原始消息（add_messages reducer 支持按 id 删除）。
    摘要失败则退回 trim 兜底（同样删旧消息但不加摘要）。容错，不阻断主流程。
    """
    messages = state.get("messages") or []
    if len(messages) <= KEEP_RECENT_MESSAGES:
        return {}
    from app.writing_memory import approx_tokens
    if approx_tokens(messages) < COMPRESS_TOKEN_THRESHOLD:
        return {}
    try:
        from langchain_core.messages import RemoveMessage, SystemMessage
        from app.writing_memory import summarize_dialogue

        old, recent = messages[:-KEEP_RECENT_MESSAGES], messages[-KEEP_RECENT_MESSAGES:]
        summary = summarize_dialogue(old, model_key=(state.get("model_preferences") or {}).get("chat"))
        updates: list = [RemoveMessage(id=m.id) for m in old if getattr(m, "id", None)]
        actions = list(state.get("actions") or [])
        if summary:
            updates.append(SystemMessage(content=f"【前情摘要】{summary}"))
            actions.append(f"compress_memory(summarized,{len(old)})")
        else:
            # 摘要失败：仅删旧消息（trim 兜底效果），不插摘要
            actions.append(f"compress_memory(trimmed,{len(old)})")
        return {"messages": updates, "actions": actions}
    except Exception:
        return {}


def node_build_index(state: WritingState) -> WritingState:
    data = writing_tools.build_semantic_index(novel_id=state.get("novel_id"))
    return {"data": data, "actions": ["build_semantic_index"]}


def node_review(state: WritingState) -> WritingState:
    chapter = state.get("chapter")
    if not chapter:
        raise writing_tools.WritingToolError("章节审查需要填写章节号")
    data = writing_tools.pre_review_chapter(chapter, novel_id=state.get("novel_id"))
    return {"data": data, "actions": ["pre_review_chapter"]}


def node_assemble(state: WritingState) -> WritingState:
    message = state.get("user_message", "")
    if not message.strip():
        raise writing_tools.WritingToolError("材料组装需要输入检索提示")
    data = writing_tools.assemble_material(
        chapter=state.get("chapter"), query=message, task=state.get("task", "prose"),
        novel_id=state.get("novel_id"),
    )
    return {"data": data, "actions": ["assemble_material"]}


def node_search(state: WritingState) -> WritingState:
    data = writing_tools.search_references(
        state.get("user_message", ""),
        dimension=state.get("dimension"),
        top_k=state.get("top_k", 8),
        novel_id=state.get("novel_id"),
    )
    return {"intent": "search", "data": data, "actions": ["search_references"]}


# ---- draft/revise 分支：材料组装 → 生成 → 预审查门禁 → 模型交叉审查（带回环）----

def node_draft_assemble(state: WritingState) -> WritingState:
    """组装材料（含 spec/recompose_instruction），结果存入 state.bundle 供生成使用。

    M2/M4：按 task 裁剪——仅章节绑定环节（beat_sheet/prose/expansion/fix）注入跨章节进展；
    character/outline 跳过（人物全局、大纲静态）。
    """
    message = state.get("user_message", "") or ""
    task = state.get("task", "prose")
    chapter = state.get("chapter")
    analysis = state.get("request_analysis") or {}
    target_chapter = analysis.get("target_chapter")
    if isinstance(target_chapter, int) and target_chapter > 0:
        chapter = target_chapter
    assembly_query = _assembly_query_from_analysis(message=message, task=task, chapter=chapter, analysis=analysis)
    data = writing_tools.assemble_material(chapter=chapter, query=assembly_query, task=task,
                                           novel_id=state.get("novel_id"))
    bundle = data.get("bundle") or {}
    bundle["novel_id"] = state.get("novel_id")
    bundle["project_kind"] = bundle.get("project_kind") or state.get("project_kind")
    bundle["user_request"] = message
    bundle["task"] = task
    bundle["request_analysis"] = analysis
    bundle["chapter"] = chapter
    if analysis.get("prose_locations"):
        bundle["prose_locations"] = analysis.get("prose_locations") or []
    if analysis.get("stage_profile"):
        bundle["stage_profile"] = analysis.get("stage_profile")
    workflow_sop = sop_for_task(bundle.get("project_kind"), task)
    bundle["workflow_sop"] = workflow_sop
    bundle["model_preferences"] = state.get("model_preferences") or {}
    actions = list(state.get("actions") or [])
    actions.append("assemble_material")
    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
    except Exception:
        writer = None

    def emit_stage(stage: str, status: str = "running", **details: Any) -> None:
        if not writer:
            return
        try:
            writer({"type": "stage", "stage": stage, "status": status, **details})
        except Exception:
            pass

    if data.get("fallback"):
        actions.append(f"assemble_fallback({(data.get('fallback') or {}).get('reason')})")
    elif data.get("generic_branch"):
        actions.append("assemble_generic")
    actions.append(f"sop({workflow_sop.get('stage')})")

    # LLM 请求理解会告诉图需要哪些章节材料进入对照上下文。
    context_chapters = analysis.get("context_chapters") if isinstance(analysis, dict) else []
    if task == "outline" and isinstance(context_chapters, list):
        outline_context = _load_outline_context(state.get("novel_id"), context_chapters)
        if outline_context:
            materials = bundle.get("materials") or {}
            materials["outline_context"] = outline_context
            bundle["materials"] = materials
            actions.append(f"outline_context({','.join(str(ch) for ch in context_chapters)})")

    if analysis.get("prose_locations"):
        materials = bundle.get("materials") or {}
        materials["target_prose_locations"] = analysis.get("prose_locations") or []
        bundle["materials"] = materials
        actions.append(f"prose_located({len(analysis.get('prose_locations') or [])})")

    # 跨章节进展记忆：仅章节环节、且非首章时注入；按关联性裁剪（相邻+强关联）。
    if task in {"beat_sheet", "prose", "expansion", "fix"} and chapter and chapter > 1:
        try:
            from app.chapter_summary import relevant_summaries
            materials = bundle.get("materials") or {}
            char_text = materials.get("character_profiles") or ""
            chars = re.findall(r"[一-龥]{2,4}", char_text)[:20] if char_text else []
            summaries = relevant_summaries(chapter, characters=chars, hints=message,
                                           novel_id=state.get("novel_id"))
            if summaries:
                bundle["cross_chapter"] = summaries
                actions.append(f"load_progress({len(summaries)})")
        except Exception:
            pass
        # RAG 语义召回：从产出向量库召回既往已确认内容（与关键词召回并存，伏笔不漏）。
        try:
            from app.output_index import query_outputs
            hits = query_outputs(message or task, n_results=5, novel_id=state.get("novel_id"))
            # 排除本章自身的命中，避免自我召回
            hits = [h for h in hits if (h.get("meta") or {}).get("chapter") != chapter]
            if hits:
                bundle["output_recall"] = hits
                actions.append(f"output_recall({len(hits)})")
        except Exception:
            pass

    # 长期创作设定（人物卡/约束/偏好）：按 track 注入，所有环节通用。
    try:
        from app.writing_memory import load_settings
        settings = load_settings(state.get("track", "normal"), project=state.get("novel_id") or "writing")
        if settings:
            bundle["long_term_settings"] = settings
            actions.append(f"load_settings({len(settings)})")
    except Exception:
        pass

    # LLM Wiki：人工确认后的稳定规则/项目共识，高权威注入所有创作环节。
    try:
        from app.writing_wiki import recall_wiki
        wiki_items = recall_wiki(
            state.get("novel_id"),
            query=message,
            task=task,
            limit=8,
        )
        if wiki_items:
            bundle["wiki_items"] = wiki_items
            actions.append(f"wiki_recall({len(wiki_items)})")
    except Exception:
        pass

    # 项目级动态 Wiki：项目状态、过程备注、待办、材料索引和项目内决定。
    # 它不承担高权威规则职责；高权威稳定共识仍由 LLM Wiki 注入。
    try:
        from app.project_wiki import recall_project_wiki
        project_wiki_items = recall_project_wiki(
            state.get("novel_id"),
            query=message,
            task=task,
            limit=8,
        )
        if project_wiki_items:
            bundle["project_wiki_items"] = project_wiki_items
            actions.append(f"project_wiki_recall({len(project_wiki_items)})")
    except Exception:
        pass

    # 当前项目资产/参考材料：只在创作流材料组装后注入，不影响普通聊天。
    try:
        project_assets = writing_tools.collect_project_assets(
            state.get("novel_id"),
            query=assembly_query or message,
            limit=12,
        )
        if project_assets.get("files") or project_assets.get("text_excerpts"):
            materials = bundle.get("materials") or {}
            materials["project_assets"] = project_assets
            if project_assets.get("text_excerpts"):
                materials["project_asset_excerpts"] = project_assets.get("text_excerpts") or []
            bundle["materials"] = materials
            actions.append(
                "project_assets("
                f"{len(project_assets.get('files') or [])},"
                f"{len(project_assets.get('text_excerpts') or [])})"
            )
    except Exception as exc:
        actions.append(f"project_assets_failed({type(exc).__name__})")

    # 创作状态卡/伏笔账本：只在创作链路注入，后续由阶段 profile 决定是否进入本轮 prompt。
    creative_state: dict[str, Any] = {}
    emit_stage("creative_state", "running")
    try:
        from app.creative_state import load_creative_state

        creative_state = load_creative_state(
            state.get("novel_id"),
            project_kind=bundle.get("project_kind") or state.get("project_kind"),
            task=task,
        )
        if creative_state.get("available"):
            bundle["creative_state"] = creative_state
            actions.append(f"creative_state({len(creative_state.get('items') or [])})")
        emit_stage("creative_state", "done", available=bool(creative_state.get("available")), count=len(creative_state.get("items") or []))
    except Exception as exc:
        creative_state = {}
        actions.append(f"creative_state_failed({type(exc).__name__})")
        emit_stage("creative_state", "done", available=False, error=type(exc).__name__)

    # 公共创作方法论与前置检查：方法层只提供流程法则和检查点，不提供剧情片段。
    emit_stage("methodology_context", "running")
    try:
        from app.novel_methodology import creative_preflight, methodology_context_for_task

        methodology_ctx = methodology_context_for_task(
            query="\n".join(filter(None, [
                assembly_query,
                str(analysis.get("generator_instruction") or ""),
                str(analysis.get("reason") or ""),
            ])),
            project_kind=bundle.get("project_kind") or state.get("project_kind") or "",
            task=task,
            creative_stage=str(analysis.get("creative_stage") or ""),
            max_lines=6,
        )
        if methodology_ctx.get("ok"):
            bundle["methodology_context"] = methodology_ctx
            actions.append(f"methodology({methodology_ctx.get('mode')},{len(methodology_ctx.get('lines') or [])})")
        preflight = creative_preflight(
            query=assembly_query or message,
            project_kind=bundle.get("project_kind") or state.get("project_kind") or "",
            task=task,
            creative_stage=str(analysis.get("creative_stage") or ""),
            material_signals=(creative_state.get("signals") if isinstance(creative_state, dict) else {}) or {},
        )
        if preflight.get("checks"):
            bundle["creative_preflight"] = preflight
            actions.append(f"creative_preflight({preflight.get('level')},{len(preflight.get('checks') or [])})")
        emit_stage(
            "methodology_context",
            "done",
            laws=len((bundle.get("methodology_context") or {}).get("lines") or []),
            checks=len((bundle.get("creative_preflight") or {}).get("checks") or []),
        )
    except Exception as exc:
        actions.append(f"methodology_failed({type(exc).__name__})")
        emit_stage("methodology_context", "done", error=type(exc).__name__)

    # 多维参考库方法维度召唤：按任务/阶段主动检索 method_*，供拆解卡、材料健康和审查共用。
    emit_stage("methodology_context", "running", detail="method_reference_recall")
    try:
        method_refs = writing_tools.collect_method_reference_materials(
            "\n".join(filter(None, [
                assembly_query or message,
                str(analysis.get("generator_instruction") or ""),
                str(analysis.get("reason") or ""),
            ])),
            project_kind_value=bundle.get("project_kind") or state.get("project_kind") or "",
            task=task,
            creative_stage=str(analysis.get("creative_stage") or ""),
            novel_id=state.get("novel_id"),
            top_k=8,
        )
        bundle["method_reference_results"] = method_refs
        if method_refs.get("results"):
            actions.append(
                "method_reference_recall("
                f"{method_refs.get('engine')},{len(method_refs.get('results') or [])},"
                f"dims={len(method_refs.get('coverage') or {})})"
            )
        emit_stage(
            "methodology_context",
            "done",
            method_refs=len(method_refs.get("results") or []),
            method_dims=len(method_refs.get("coverage") or {}),
        )
    except Exception as exc:
        bundle["method_reference_results"] = {"ok": False, "results": [], "error": f"{type(exc).__name__}: {exc}"}
        actions.append(f"method_reference_recall_failed({type(exc).__name__})")
        emit_stage("methodology_context", "done", method_refs=0, error=type(exc).__name__)

    emit_stage("creative_enhancements", "running")
    try:
        from app.creative_enhancements import build_creative_enhancements

        enhancements = build_creative_enhancements(
            project_kind=bundle.get("project_kind") or state.get("project_kind") or "",
            task=task,
            query=assembly_query or message,
            analysis=analysis,
            bundle=bundle,
        )
        for key in enhancements.get("keys") or []:
            value = enhancements.get(key)
            if value:
                bundle[key] = value
        if enhancements.get("keys"):
            actions.append("creative_enhancements(" + ",".join(enhancements.get("keys") or []) + ")")
        emit_stage("creative_enhancements", "done", keys=enhancements.get("keys") or [])
    except Exception as exc:
        actions.append(f"creative_enhancements_failed({type(exc).__name__})")
        emit_stage("creative_enhancements", "done", error=type(exc).__name__)

    emit_stage("material_profile", "running")
    try:
        bundle, material_profile = apply_stage_material_profile(
            bundle,
            analysis=analysis,
            project_kind=bundle.get("project_kind") or state.get("project_kind"),
            task=task,
        )
        if material_profile.get("applied"):
            actions.append(
                "material_profile("
                f"{material_profile.get('stage')},"
                f"removed={len(material_profile.get('removed') or [])})"
            )
        emit_stage(
            "material_profile",
            "done",
            stage=material_profile.get("stage") or "",
            removed=len(material_profile.get("removed") or []),
        )
    except Exception as exc:
        bundle["material_profile"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        actions.append(f"material_profile_failed({type(exc).__name__})")
        emit_stage("material_profile", "done", error=type(exc).__name__)

    emit_stage("context_broker", "running")
    try:
        from app.writing_context_broker import attach_context_broker

        bundle = attach_context_broker(
            bundle,
            task=task,
            project_kind=bundle.get("project_kind") or state.get("project_kind"),
        )
        broker_summary = (bundle.get("context_broker") or {}).get("summary") or {}
        actions.append(
            "context_broker("
            f"selected={broker_summary.get('selected', 0)},"
            f"dropped={broker_summary.get('dropped', 0)})"
        )
        emit_stage(
            "context_broker",
            "done",
            selected=broker_summary.get("selected", 0),
            dropped=broker_summary.get("dropped", 0),
            tokens=broker_summary.get("estimated_tokens", 0),
        )
    except Exception as exc:
        bundle["context_broker"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        actions.append(f"context_broker_failed({type(exc).__name__})")
        emit_stage("context_broker", "done", error=type(exc).__name__)

    try:
        material_health = writing_tools.assess_material_health(bundle, task=task, chapter=chapter)
        bundle["material_health"] = material_health
        if material_health.get("level") == "warn":
            actions.append(f"material_health(warn,{len(material_health.get('warnings') or [])})")
    except Exception as exc:
        bundle["material_health"] = {"ok": False, "level": "warn", "error": f"{type(exc).__name__}: {exc}"}
        actions.append("material_health_failed")

    bundle["request_text"] = message
    _flow_debug(
        f"draft_assemble novel={state.get('novel_id')} kind={state.get('project_kind')} "
        f"task={task} chapter={chapter} query_len={len(assembly_query)}"
    )
    try:
        writing_tools.rewrite_material_bundle_output(data, bundle)
    except Exception:
        pass
    return {
        "bundle": bundle,
        "chapter": chapter,
        "workflow_sop": workflow_sop,
        "actions": actions,
    }


def _assembly_query_from_analysis(*, message: str, task: str, chapter: int | None, analysis: dict[str, Any]) -> str:
    """Build a material-retrieval query from the LLM intent analysis, not only raw user text."""
    parts: list[str] = []
    if message:
        parts.append(message)
    if chapter:
        parts.append(f"第{chapter}章")
    for key in ("generator_instruction", "intent", "deliverable", "answer_style"):
        value = analysis.get(key) if isinstance(analysis, dict) else None
        if value:
            parts.append(str(value))
    for key in ("entities", "affected_materials", "affected_files", "context_chapters"):
        value = analysis.get(key) if isinstance(analysis, dict) else None
        if isinstance(value, list):
            parts.extend(str(item) for item in value[:12])
    parts.append(task)
    seen: set[str] = set()
    compact: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        compact.append(text)
    return "；".join(compact)[:1200]


def node_generate(state: WritingState) -> WritingState:
    """使用用户选择的创作模型，根据已组装材料生成或修订内容。"""
    bundle = state.get("bundle") or {}
    actions = list(state.get("actions") or [])
    feedback = ""
    mr = state.get("model_review") or {}
    pr = state.get("pre_review") or {}
    if mr and not mr.get("passed", True):
        feedback = review_feedback_text(mr)
    elif pr and pr.get("blocking_count", 0) > 0:
        feedback = writing_tools.pre_review_issues_text(pr.get("issues") or [])
    out = generate_prose(
        bundle,
        model_key=(state.get("model_preferences") or {}).get("writing"),
        revise_target=state.get("draft", "") if state.get("intent") == "revise" else "",
        review_feedback=feedback,
    )
    actions.append(f"generate({out.get('model')})")
    return {
        "draft": out.get("text", ""),
        "actions": actions,
        "iterations": state.get("iterations", 0) + 1,
    }


def node_pre_review(state: WritingState) -> WritingState:
    _emit_graph_stage("pre_review", "running")
    if is_novel_planning_task(state.get("project_kind"), state.get("task")):
        actions = list(state.get("actions") or [])
        actions.append("pre_review_skipped(novel_planning)")
        _emit_graph_stage("pre_review", "done", skipped=True, reason="novel_planning", blocking_count=0)
        return {"pre_review": {"ok": True, "passed": True, "blocking_count": 0, "issues": []}, "actions": actions}
    if state.get("project_kind") in {"short_film", "generic"}:
        actions = list(state.get("actions") or [])
        actions.append("pre_review_skipped(generic)")
        _emit_graph_stage("pre_review", "done", skipped=True, reason="generic", blocking_count=0)
        return {"pre_review": {"ok": True, "passed": True, "blocking_count": 0, "issues": []}, "actions": actions}
    bundle = state.get("bundle") or {}
    outline = (bundle.get("materials") or {}).get("chapter_outline", "") or ""
    pr = writing_tools.pre_review_text(state.get("draft", ""), outline=outline)
    actions = list(state.get("actions") or [])
    actions.append("pre_review_text")
    _emit_graph_stage(
        "pre_review",
        "done",
        passed=bool(pr.get("passed", True)),
        blocking_count=int(pr.get("blocking_count") or 0),
        warning_count=int(pr.get("warning_count") or 0),
    )
    return {"pre_review": pr, "actions": actions}


def _enhancement_brief(bundle: dict[str, Any]) -> str:
    try:
        from app.creative_enhancements import format_enhancement_blocks

        return format_enhancement_blocks(bundle)[:1800]
    except Exception:
        return ""


def node_model_review(state: WritingState) -> WritingState:
    _emit_graph_stage("model_review", "running")
    bundle = state.get("bundle") or {}
    materials = bundle.get("materials") or {}
    technique_context = bundle.get("technique_context") or (state.get("merge_info") or {}).get("technique_context") or {}
    if not technique_context:
        try:
            from app.writing_techniques import technique_context_for_task

            request_analysis = state.get("request_analysis") or bundle.get("request_analysis") or {}
            technique_context = technique_context_for_task(
                query="\n".join(filter(None, [
                    str(state.get("user_message") or bundle.get("user_request") or ""),
                    str(request_analysis.get("generator_instruction") or ""),
                    str(request_analysis.get("reason") or ""),
                ])),
                outline="\n".join(filter(None, [
                    str(materials.get("chapter_outline") or ""),
                    str(materials.get("outline_context") or ""),
                ])),
                project_kind=state.get("project_kind") or bundle.get("project_kind") or "",
                task=state.get("task") or bundle.get("task") or "",
                model_key=(state.get("model_preferences") or {}).get("review"),
                max_lines=6,
            )
            if technique_context.get("ok"):
                bundle["technique_context"] = technique_context
        except Exception:
            technique_context = {}
    strategy = decide_review_strategy(
        project_kind=state.get("project_kind"),
        task=state.get("task"),
        draft=state.get("draft", ""),
        need_audit=state.get("need_audit") or {},
    )
    if strategy.get("mode") == "skip":
        actions = list(state.get("actions") or [])
        actions.append(f"model_review_skipped({strategy.get('reason')})")
        _emit_graph_stage("model_review", "done", skipped=True, mode="skip", reason=strategy.get("reason"))
        return {
            "review_strategy": strategy,
            "model_review": {"passed": True, "overall_score": 0, "model": "skipped", "strategy": strategy},
            "actions": actions,
        }
    if strategy.get("mode") == "deterministic_checklist":
        actions = list(state.get("actions") or [])
        review = deterministic_review(
            project_kind=state.get("project_kind"),
            task=state.get("task"),
            draft=state.get("draft", ""),
            strategy=strategy,
            technique_context=technique_context,
            creative_preflight=bundle.get("creative_preflight") or {},
            methodology_context=bundle.get("methodology_context") or {},
            creative_enhancements={
                key: bundle.get(key) or {}
                for key in (
                    "reference_cards",
                    "chapter_function",
                    "reader_experience",
                    "packaging_context",
                    "research_brief",
                    "self_check_loop",
                    "humanization_check",
                )
            },
        )
        actions.append(f"model_review({review.get('model')})")
        _emit_graph_stage(
            "model_review",
            "done",
            mode="deterministic_checklist",
            model=review.get("model"),
            passed=bool(review.get("passed", True)),
            overall_score=int(review.get("overall_score") or 0),
        )
        return {"review_strategy": strategy, "model_review": review, "actions": actions}
    mr = model_review_cross(
        state.get("draft", ""),
        outline=materials.get("chapter_outline", "") or "",
        characters=materials.get("character_profiles", "") or "",
        technique_context=(technique_context or {}).get("text", "") if isinstance(technique_context, dict) else str(technique_context or ""),
        model_key=(state.get("model_preferences") or {}).get("review"),
    )
    mr["strategy"] = strategy
    if technique_context:
        mr["technique_context"] = technique_context
    actions = list(state.get("actions") or [])
    actions.append(f"model_review({mr.get('model')})")
    _emit_graph_stage(
        "model_review",
        "done",
        mode=strategy.get("mode"),
        model=mr.get("model"),
        passed=bool(mr.get("passed", True)),
        overall_score=int(mr.get("overall_score") or 0),
    )
    return {"review_strategy": strategy, "model_review": mr, "actions": actions}


def node_draft_finalize(state: WritingState) -> WritingState:
    """把 draft/revise 的产出整理成 data，answer 由 WritingAgent 格式化。

    记忆写入只在用户确认后进行：
    - 正文确认/文件保存后由 file_update_flow / confirm_writeback 生成章节摘要与 RAG 索引。
    - 人物/大纲确认后由 Web intervene 写入长期 Store。
    这里不把未确认生成稿写入长期记忆，避免 rejected draft 污染后续创作。
    """
    task = state.get("task", "prose")
    draft = state.get("draft", "")
    try:
        from app.final_text_cleaner import clean_final_draft

        draft = clean_final_draft(draft, task=task, project_kind=state.get("project_kind", ""))
    except Exception:
        pass
    chapter = state.get("chapter")
    pr = state.get("pre_review") or {}
    actions = list(state.get("actions") or [])
    if draft and task in {"prose", "character", "outline"}:
        actions.append("memory_write_pending_user_confirm")
    return {
        "actions": actions,
        "data": {
            "draft": draft,
            "archive_content": _archive_content_for_finalize(
                task=task,
                chapter=chapter,
                draft=draft,
                project_kind=state.get("project_kind", ""),
            ),
            "task": task,
            "chapter": chapter,
            "pre_review": pr,
            "model_review": state.get("model_review") or {},
            "review_strategy": state.get("review_strategy") or {},
            "iterations": state.get("iterations", 0),
            "merge_info": state.get("merge_info") or {},
            "need_audit": state.get("need_audit") or {},
            "material_health": (state.get("bundle") or {}).get("material_health") or {},
            "request_analysis": state.get("request_analysis") or {},
            "prose_locations": (state.get("bundle") or {}).get("prose_locations") or (state.get("request_analysis") or {}).get("prose_locations") or [],
            "pending_intent": state.get("pending_intent") or {},
            "workflow_sop": state.get("workflow_sop") or (state.get("bundle") or {}).get("workflow_sop") or {},
            "project_kind": state.get("project_kind", ""),
            "project_init": state.get("project_init") or {},
            "invocation_id": state.get("invocation_id", ""),
            "technique_context": (state.get("bundle") or {}).get("technique_context") or (state.get("merge_info") or {}).get("technique_context") or {},
        },
    }


def _archive_content_for_finalize(*, task: str, chapter: int | None, draft: str, project_kind: str) -> str:
    if project_kind == "novel_strong" and task == "outline" and chapter:
        try:
            from app.outline_writeback import clean_outline_archive_content

            cleaned = clean_outline_archive_content(draft, chapter)
            return cleaned or draft
        except Exception:
            return draft
    return draft


def _after_pre_review(state: WritingState) -> str:
    """预审查门禁：blocking>0 且未到回环上限 → 回 generate 重新生成；否则进模型审查。

    生成结果为空时直接结束，避免对空文本继续审查。
    """
    if not (state.get("draft") or "").strip():
        return "finalize"
    pr = state.get("pre_review") or {}
    if pr.get("blocking_count", 0) > 0 and state.get("iterations", 0) < MAX_ITERATIONS:
        return "regen"
    return "model_review"


def _after_model_review(state: WritingState) -> str:
    """模型审查门禁：未过且未到上限 → 回 assemble 补材料重组；否则结束。"""
    mr = state.get("model_review") or {}
    if not mr.get("passed", True) and state.get("iterations", 0) < MAX_ITERATIONS:
        return "regen"
    return "finalize"


def _route(state: WritingState) -> str:
    return state.get("intent", "search")


def node_draft_entry(state: WritingState) -> WritingState:
    """draft/revise 分支的占位入口（不改状态），仅用于条件分流。"""
    return {}


def build_graph():
    """构建并编译写作工作流图（阶段 A 路由 + 阶段 B 生成/审查回环）。"""
    graph = StateGraph(WritingState)
    graph.add_node("route_intent", route_intent)
    graph.add_node("compress_memory", node_compress_memory)
    graph.add_node("prepare_project", node_prepare_project)
    graph.add_node("request_analyze", node_request_analyze)
    graph.add_node("build_index", node_build_index)
    graph.add_node("review", node_review)
    graph.add_node("assemble", node_assemble)
    graph.add_node("search", node_search)
    # draft/revise 分支
    graph.add_node("draft_entry", node_draft_entry)
    graph.add_node("need_audit", node_need_audit)
    graph.add_node("draft_assemble", node_draft_assemble)
    graph.add_node("generate", node_generate)
    graph.add_node("pre_review", node_pre_review)
    graph.add_node("model_review", node_model_review)
    graph.add_node("draft_finalize", node_draft_finalize)

    # 用户提问进入图后，第一节点先用 LLM 理解真实意图，再做记忆压缩、项目准备与流程路由。
    graph.set_entry_point("request_analyze")
    graph.add_edge("request_analyze", "compress_memory")
    graph.add_edge("compress_memory", "prepare_project")
    graph.add_edge("prepare_project", "route_intent")
    graph.add_conditional_edges(
        "route_intent",
        _route,
        {
            "build_index": "build_index",
            "review": "review",
            "assemble": "assemble",
            "search": "search",
            "draft": "draft_entry",
            "revise": "draft_entry",
        },
    )
    for terminal in ("build_index", "review", "assemble", "search"):
        graph.add_edge(terminal, END)

    # draft/revise：统一组装项目材料后进入本地/API 模型生成。
    graph.add_edge("draft_entry", "need_audit")
    graph.add_edge("need_audit", "draft_assemble")
    graph.add_edge("draft_assemble", "generate")
    # generate 后两道门禁回环
    graph.add_edge("generate", "pre_review")
    # 审查回环回到 generate，复用本轮已组装材料。
    graph.add_conditional_edges(
        "pre_review", _after_pre_review,
        {"regen": "generate", "model_review": "model_review", "finalize": "draft_finalize"},
    )
    graph.add_conditional_edges(
        "model_review", _after_model_review,
        {"regen": "generate", "finalize": "draft_finalize"},
    )
    graph.add_edge("draft_finalize", END)
    # M1：接入 checkpointer，按 thread_id 持久化短期对话记忆（重启/刷新可恢复）。
    return graph.compile(checkpointer=get_checkpointer())


_GRAPH = None
_GRAPH_LOCK = __import__("threading").Lock()


def get_graph():
    """惰性编译并缓存 graph（编译一次复用，加锁防并发首次初始化竞态）。"""
    global _GRAPH
    if _GRAPH is not None:
        return _GRAPH
    with _GRAPH_LOCK:
        if _GRAPH is None:
            _GRAPH = build_graph()
    return _GRAPH


def graph_visualization(project_kind: str = "generic") -> dict[str, Any]:
    """Return a read-only native LangGraph view for the Web UI.

    The runtime backbone comes from the compiled LangGraph object. The visual
    profile then narrows that shared backbone to the selected project kind and
    adds type capabilities that live outside the core text graph, such as short
    film visual prompt/image generation or casual-project idea settlement.
    """
    from app.workflow_status import STAGE_LABELS

    normalized_kind = project_kind if project_kind in PROJECT_GRAPH_PROFILES else "generic"
    profile = PROJECT_GRAPH_PROFILES[normalized_kind]
    graph = get_graph().get_graph(xray=True)
    visible = profile.get("visible")
    visible_ids: set[str] | None = set(visible) if visible else None
    extra_nodes = set(profile.get("extra_nodes") or [])
    if visible_ids is not None:
        visible_ids |= extra_nodes
    nodes: list[dict[str, Any]] = []
    for node_id in [*graph.nodes.keys(), *profile.get("extra_nodes", [])]:
        if visible_ids is not None and node_id not in visible_ids:
            continue
        label = STAGE_LABELS.get(node_id) or {
            "__start__": "开始",
            "__end__": "结束",
            "route_intent": "意图路由",
            "compress_memory": "记忆压缩",
            "prepare_project": "项目准备",
            "novel_stage_route": "阶段标识",
            "project_wiki_route": "Wiki路由索引",
            "build_index": "构建索引",
            "review": "章节审查",
            "assemble": "材料组装",
            "search": "参考检索",
            "draft_entry": "创作入口",
            "material_profile": "材料策略切换",
            "user_confirm": "用户确认",
            "archive_write": "归档写回",
            "project_wiki_archive": "Wiki归档摘要",
            "idea_settle": "灵感沉淀",
            "visual_prompt": "分镜提示词",
            "image_plan": "生图参数",
            "image_generate": "生成画面",
            "storyboard_archive": "影像归档",
        }.get(node_id, node_id)
        x, y = dict(GRAPH_BASE_LAYOUT, **(profile.get("layout") or {})).get(
            node_id,
            (160 + (len(nodes) % 5) * 200, 1640 + (len(nodes) // 5) * 110),
        )
        nodes.append({
            "id": node_id,
            "label": label,
            "group": GRAPH_NODE_GROUPS.get(node_id, "其他"),
            "description": GRAPH_NODE_DESCRIPTIONS.get(node_id, ""),
            "system": node_id in {"__start__", "__end__"},
            "lightweight": normalized_kind in {"short_film", "generic"} and node_id == "pre_review",
            "type_capability": node_id in extra_nodes,
            "position": {"x": x, "y": y},
        })

    node_ids = {node["id"] for node in nodes}
    edges = [
        {
            "source": edge.source,
            "target": edge.target,
            "label": str(edge.data or ""),
            "conditional": bool(edge.conditional),
        }
        for edge in graph.edges
        if edge.source in node_ids and edge.target in node_ids
    ]
    edges.extend([
        edge for edge in profile.get("extra_edges", [])
        if edge.get("source") in node_ids and edge.get("target") in node_ids
    ])
    group_order = ["系统边界", "入口理解", "路由分支", "创作主线", "审查回环", "定稿确认", "其他"]
    for node in nodes:
        if node["group"] not in group_order:
            group_order.insert(-1, node["group"])
    groups = [
        {"name": group, "nodes": [node["id"] for node in nodes if node["group"] == group]}
        for group in group_order
        if any(node["group"] == group for node in nodes)
    ]
    return {
        "ok": True,
        "project_kind": normalized_kind,
        "mermaid": _visual_mermaid(nodes, edges),
        "nodes": nodes,
        "edges": edges,
        "groups": groups,
        "canvas": GRAPH_CANVAS,
        "group_bands": profile.get("group_bands") or GRAPH_BASE_BANDS,
        "notes": PROJECT_KIND_GRAPH_NOTES.get(normalized_kind) or PROJECT_KIND_GRAPH_NOTES["generic"],
    }
