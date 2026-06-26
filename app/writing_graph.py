from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from app import writing_tools
from app.config import ROOT
from app.project_paths import outputs_dir
from app.project_kinds import SHORT_FILM_KIND
from app.writing_sop import sop_for_task
from app.writing_generate import generate_prose
from app.writing_harness import check_request_text, decide_provider_route, estimate_budget
from app.writing_memory import get_checkpointer
from app.writing_model_review import model_review_cross, review_feedback_text
from app.writing_need_audit import audit_need
from app.writing_review_strategy import decide_review_strategy, deterministic_review


# 回环上限：审查不过最多回补材料重组 2 次（规范要求，防无限烧钱）。
MAX_ITERATIONS = 2
# LangGraph 递归保险：正常创作流远低于该值，未来新增边时用于兜底防无限循环。
GRAPH_RECURSION_LIMIT = 30
# M3：对话消息超过此 token 阈值时，自动把旧消息压成前情摘要，保留最近 KEEP_RECENT 条。
COMPRESS_TOKEN_THRESHOLD = 4000
KEEP_RECENT_MESSAGES = 6


GRAPH_NODE_DESCRIPTIONS: dict[str, str] = {
    "__start__": "LangGraph 入口。",
    "request_analyze": "使用 LLM 理解用户提问、项目类型、章节、目标文件与流程入口。",
    "compress_memory": "当上下文过长时压缩旧消息，保留可恢复的短期任务记忆。",
    "prepare_project": "检查项目类型与结构，必要时初始化项目规范目录。",
    "route_intent": "根据意图分析结果进入搜索、材料组装、审查、索引或创作分支。",
    "build_index": "重建参考资料与语义检索索引。",
    "review": "执行章节或材料审查。",
    "assemble": "只组装材料，不生成正文。",
    "search": "检索参考资料、五维库或项目相关材料。",
    "draft_entry": "进入创作或改写分支。",
    "need_audit": "审计需求复杂度、材料依赖和流程风险。",
    "context_followup": "网页模型固定会话续问时复用上下文。",
    "draft_assemble": "按项目 Wiki、章节、人物、前情、技法与限制精确组装材料。",
    "prompt_refine": "把材料与目标整理成可执行的专业任务单。",
    "provider_route": "判断是否进入网页模型 provider 分支。",
    "provider_fanout": "调用已勾选网页 provider，收集外部候选内容。",
    "provider_confirm_gate": "等待用户确认或补充 provider 材料后再继续融合。",
    "generate": "调用创作模型生成或根据审查反馈重新生成。",
    "pre_review": "规则预审，发现硬性问题时进入回环修复。",
    "model_review": "审查模型评分与反馈，必要时触发重新生成。",
    "draft_finalize": "清洗定稿内容，准备用户确认与后续归档。",
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
    "compress_memory": "入口理解",
    "prepare_project": "入口理解",
    "route_intent": "入口理解",
    "build_index": "路由分支",
    "review": "路由分支",
    "assemble": "路由分支",
    "search": "路由分支",
    "draft_entry": "创作主线",
    "need_audit": "创作主线",
    "context_followup": "创作主线",
    "draft_assemble": "创作主线",
    "prompt_refine": "创作主线",
    "provider_route": "创作主线",
    "provider_fanout": "网页模型",
    "provider_confirm_gate": "网页模型",
    "generate": "审查回环",
    "pre_review": "审查回环",
    "model_review": "审查回环",
    "draft_finalize": "定稿确认",
    "idea_settle": "定稿确认",
    "visual_prompt": "影像生图",
    "image_plan": "影像生图",
    "image_generate": "影像生图",
    "storyboard_archive": "影像生图",
}


PROJECT_KIND_GRAPH_NOTES: dict[str, list[str]] = {
    "novel_strong": [
        "小说项目默认走完整创作链路：意图分析、材料装配、生成、规则预审、模型审查、定稿确认。",
        "章节正文、大纲、人物、设定等写回动作在用户确认后由归档流程执行，不在 LangGraph 内静默覆盖文件。",
    ],
    "short_film": [
        "电影脚本项目复用主创作链路，剧本、节拍、分镜提示词和生图由项目类型能力补充。",
        "规则预审对短片类型通常降级为轻量检查，重点由需求审计、模型审查和用户确认兜底。",
    ],
    "generic": [
        "随想项目复用意图分析、材料整理、生成与确认链路，适合灵感、草稿和参考材料整理。",
        "规则预审通常轻量化，避免随想类任务被小说专用规则过度约束。",
    ],
}


GRAPH_CANVAS = {"width": 1420, "height": 1600}
GRAPH_BASE_LAYOUT: dict[str, tuple[int, int]] = {
    "__start__": (660, 44),
    "request_analyze": (660, 140),
    "compress_memory": (660, 236),
    "prepare_project": (660, 332),
    "route_intent": (660, 428),
    "build_index": (160, 548),
    "review": (360, 548),
    "assemble": (560, 548),
    "search": (760, 548),
    "draft_entry": (1000, 548),
    "need_audit": (1000, 654),
    "draft_assemble": (1000, 760),
    "prompt_refine": (1000, 866),
    "provider_route": (1000, 972),
    "provider_fanout": (760, 1088),
    "provider_confirm_gate": (760, 1194),
    "generate": (1200, 1088),
    "pre_review": (1200, 1194),
    "model_review": (1200, 1300),
    "draft_finalize": (1000, 1416),
    "__end__": (660, 1532),
}
GRAPH_BASE_BANDS = [
    ["入口理解", 92, 476],
    ["路由分支", 508, 592],
    ["创作主线", 620, 1010],
    ["网页模型", 1050, 1232],
    ["审查回环", 1050, 1340],
    ["定稿确认", 1378, 1570],
]

COMMON_GRAPH_NODE_IDS = {
    "__start__", "request_analyze", "compress_memory", "prepare_project", "route_intent", "__end__",
}

PROJECT_GRAPH_PROFILES: dict[str, dict[str, Any]] = {
    "novel_strong": {
        "visible": None,
        "extra_nodes": [],
        "extra_edges": [],
        "layout": {},
        "group_bands": GRAPH_BASE_BANDS,
    },
    "short_film": {
        "visible": COMMON_GRAPH_NODE_IDS | {
            "assemble", "search", "draft_entry", "need_audit", "draft_assemble",
            "prompt_refine", "provider_route", "provider_fanout", "provider_confirm_gate",
            "generate", "pre_review", "model_review", "draft_finalize",
        },
        "extra_nodes": ["visual_prompt", "image_plan", "image_generate", "storyboard_archive"],
        "extra_edges": [
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
        },
        "group_bands": [
            ["入口理解", 92, 476],
            ["剧本材料", 508, 1010],
            ["网页模型", 1050, 1232],
            ["审查回环", 1050, 1340],
            ["影像生图", 1278, 1570],
        ],
    },
    "generic": {
        "visible": COMMON_GRAPH_NODE_IDS | {
            "assemble", "search", "draft_entry", "need_audit", "draft_assemble",
            "prompt_refine", "provider_route", "generate", "model_review", "draft_finalize",
        },
        "extra_nodes": ["idea_settle"],
        "extra_edges": [
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
            "prompt_refine": (900, 866),
            "provider_route": (900, 972),
            "generate": (900, 1088),
            "model_review": (900, 1194),
            "draft_finalize": (900, 1300),
            "idea_settle": (900, 1416),
            "__end__": (660, 1532),
        },
        "group_bands": [
            ["入口理解", 92, 476],
            ["灵感材料", 508, 592],
            ["随想成稿", 620, 1242],
            ["确认沉淀", 1268, 1570],
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
    provider_answers: list[dict]
    use_provider_source: bool
    login_confirmed: dict
    skip_material_assemble: bool
    track: str
    novel_id: str
    project_kind: str
    project_init: dict
    request_analysis: dict
    pending_intent: dict
    need_audit: dict
    bundle: dict
    workflow_sop: dict
    request_file: str
    request_harness: dict
    token_budget: dict
    refined_prompt: dict
    provider_failed: bool
    provider_route: dict
    awaiting_provider_confirm: bool
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
            )
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
        use_provider_source=bool(state.get("use_provider_source")),
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


def _json_http(method: str, path: str, payload: dict[str, Any] | None = None,
               timeout: int = 30) -> dict[str, Any]:
    """Call the local web API used by the normal provider flow."""
    base = os.getenv("WRITING_WEB_BASE_URL", "http://127.0.0.1:7861").rstrip("/")
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(f"{base}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def _run_provider_job_via_web_api(payload: dict[str, Any], writer=None,
                                  poll_interval: float = 1.0,
                                  timeout_seconds: int = 210) -> dict[str, Any]:
    """Start and poll the same async provider job endpoint used by normal AI mode."""
    started = _json_http("POST", "/api/ai-providers/run-async", payload, timeout=30)
    job_id = started.get("job_id")
    if not started.get("ok") or not job_id:
        return started

    emitted: set[str] = set()
    deadline = time.monotonic() + timeout_seconds
    last_snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = _json_http("GET", f"/api/ai-providers/job/{job_id}", timeout=30)
        last_snapshot = snapshot
        for item in snapshot.get("providers") or []:
            pid = item.get("provider")
            status = item.get("status")
            if pid and status in {"success", "partial", "failed"} and pid not in emitted:
                emitted.add(pid)
                if writer:
                    try:
                        writer({"type": "provider", "provider": pid,
                                "name": item.get("name"), "status": status,
                                "result": item.get("result", ""),
                                "elapsed": item.get("elapsed_seconds")})
                    except Exception:
                        pass
        if snapshot.get("done"):
            return snapshot.get("result") or {
                "ok": any((p.get("status") in {"success", "partial"}) for p in snapshot.get("providers") or []),
                "status": "completed",
                "message": "AI provider 协同执行完成。",
                "format_for_writing": payload.get("format_for_writing", False),
                "selected": started.get("selected") or [],
                "results": snapshot.get("providers") or [],
            }
        time.sleep(poll_interval)
    return {
        "ok": False,
        "status": "timeout",
        "message": f"AI provider 协同超时，job_id={job_id}",
        "selected": started.get("selected") or [],
        "results": last_snapshot.get("providers") or [],
    }


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

def node_provider_fanout(state: WritingState) -> WritingState:
    """创作模式+AI 同开时前置执行：抓千问/DeepSeek/豆包答案作为前置素材。

    失败/空答案不阻断主流程（容错）；只取 success/partial 的非空答案。
    通过 get_stream_writer 逐家透出进度（provider_init + 每家完成事件），供前端渲染卡片。
    """
    from app.ai_provider_bridge import PROVIDERS

    try:
        from langgraph.config import get_stream_writer
        writer = get_stream_writer()
    except Exception:
        writer = None

    login = state.get("login_confirmed") or {}
    answers: list[dict] = []
    actions = list(state.get("actions") or [])
    # 优先把"提问文件"全文发给 provider（材料+规范+限制），无则退回用户原文。
    refined = state.get("refined_prompt") or {}
    bundle = state.get("bundle") or {}
    if state.get("skip_material_assemble"):
        request_text = bundle.get("request_text") or state.get("user_message", "")
    else:
        request_text = refined.get("text") or bundle.get("request_text") or state.get("user_message", "")
    if any(login.values()):
        # 按 PROVIDERS 固定顺序透出选中列表，前端据此先出占位卡片。
        order = [p["id"] for p in PROVIDERS if login.get(p["id"])]
        token_budget = estimate_budget(prompt_text=request_text, selected_providers=order)
        actions.append(f"budget({token_budget.get('level')},{token_budget.get('estimated_total_tokens')})")
        _flow_debug(
            f"provider_fanout_start novel={state.get('novel_id')} task={state.get('task')} "
            f"mode={state.get('mode')} selected={','.join(order)} prompt_len={len(request_text)} "
            f"budget={token_budget.get('estimated_total_tokens')}"
        )
        if writer:
            try:
                writer({"type": "provider_init", "order": order,
                        "names": {p["id"]: p["name"] for p in PROVIDERS if p["id"] in order}})
            except Exception:
                pass

        try:
            result = _run_provider_job_via_web_api(
                {
                    "message": request_text,
                    "mode": state.get("mode", "auto"),
                    "chapter": state.get("chapter"),
                    "attachments": [],
                    "login_confirmed": login,
                    "format_for_writing": False,  # 提问文件已是完整 prompt，不再二次包装
                    "novel_id": state.get("novel_id"),
                },
                writer=writer,
            )
            for item in result.get("results") or []:
                if item.get("status") in {"success", "partial"} and (item.get("result") or "").strip():
                    answers.append({
                        "provider": item.get("provider"),
                        "name": item.get("name"),
                        "status": item.get("status"),
                        "result": item.get("result"),
                    })
            actions.append(f"provider_fanout({len(answers)})")
            _flow_debug(
                f"provider_fanout_done novel={state.get('novel_id')} task={state.get('task')} "
                f"answers={len(answers)} statuses="
                f"{','.join((item.get('provider','?') + ':' + item.get('status','?')) for item in (result.get('results') or []))}"
            )
        except Exception as exc:  # 容错：不阻断主流程
            actions.append("provider_fanout(failed)")
            _flow_debug(
                f"provider_fanout_error novel={state.get('novel_id')} task={state.get('task')} "
                f"type={type(exc).__name__} error={exc}"
            )
            return {"provider_answers": [], "actions": actions, "error": f"provider_fanout: {exc}", "token_budget": token_budget if any(login.values()) else {}}
    return {"provider_answers": answers, "actions": actions, "token_budget": token_budget if any(login.values()) else {}}


def node_provider_confirm_gate(state: WritingState) -> WritingState:
    """Stop after provider fanout so the user can confirm/edit/upload materials."""
    actions = list(state.get("actions") or [])
    actions.append("provider_confirm_gate")
    return {
        "awaiting_provider_confirm": True,
        "actions": actions,
        "data": {
            "draft": "",
            "provider_answers": state.get("provider_answers") or [],
            "awaiting_provider_confirm": True,
            "provider_failed": False,
            "merge_info": state.get("merge_info") or {},
            "request_file": state.get("request_file", ""),
            "refined_prompt": state.get("refined_prompt") or {},
            "request_harness": state.get("request_harness") or (state.get("bundle") or {}).get("request_harness") or {},
            "token_budget": state.get("token_budget") or (state.get("bundle") or {}).get("token_budget") or {},
            "provider_route": state.get("provider_route") or {},
            "project_kind": state.get("project_kind", ""),
            "project_init": state.get("project_init") or {},
            "need_audit": state.get("need_audit") or {},
            "material_health": (state.get("bundle") or {}).get("material_health") or {},
            "request_analysis": state.get("request_analysis") or {},
            "pending_intent": state.get("pending_intent") or {},
            "invocation_id": state.get("invocation_id", ""),
            "workflow_sop": state.get("workflow_sop") or (state.get("bundle") or {}).get("workflow_sop") or {},
        },
    }


def node_context_followup(state: WritingState) -> WritingState:
    """AI 固定会话续问：跳过材料重组，直接把用户新方向发给 provider。"""
    from app.project_kinds import project_kind

    message = (state.get("user_message") or "").strip()
    task = state.get("task", "prose")
    kind = state.get("project_kind") or project_kind(state.get("novel_id"))
    bundle = {
        "task": task,
        "chapter": state.get("chapter"),
        "novel_id": state.get("novel_id"),
        "project_kind": kind,
        "materials": {},
        "spec": "",
        "recompose_instruction": "上下文续问：沿用 provider 固定会话的上下文，只处理用户本次的新方向。",
        "request_text": message,
        "model_preferences": state.get("model_preferences") or {},
    }
    workflow_sop = sop_for_task(kind, task)
    bundle["workflow_sop"] = workflow_sop
    actions = list(state.get("actions") or [])
    actions.append("skip_material_assemble(context_followup)")
    actions.append(f"sop({workflow_sop.get('stage')})")
    _flow_debug(
        f"context_followup novel={state.get('novel_id')} kind={kind} "
        f"task={task} chapter={state.get('chapter')} prompt_len={len(message)}"
    )
    return {
        "bundle": bundle,
        "workflow_sop": workflow_sop,
        "refined_prompt": {"ok": True, "source": "context_followup", "text": message},
        "actions": actions,
        "request_file": "",
    }


def node_provider_route(state: WritingState) -> WritingState:
    """P2 information-boundary router: decide fanout vs single-agent path."""
    refined = state.get("refined_prompt") or {}
    bundle = state.get("bundle") or {}
    request_text = refined.get("text") or bundle.get("request_text") or state.get("user_message", "")
    route = decide_provider_route(
        project_kind=state.get("project_kind") or bundle.get("project_kind"),
        task=state.get("task", "prose"),
        workflow_sop=state.get("workflow_sop") or bundle.get("workflow_sop") or {},
        use_provider_source=bool(state.get("use_provider_source")),
        login_confirmed=state.get("login_confirmed") or {},
        skip_material_assemble=bool(state.get("skip_material_assemble")),
        request_text=request_text,
    )
    actions = list(state.get("actions") or [])
    actions.append(f"provider_route({route.get('decision')},{route.get('reason')})")
    return {"provider_route": route, "token_budget": route.get("budget") or {}, "actions": actions}


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
    workflow_sop = sop_for_task(bundle.get("project_kind"), task)
    bundle["workflow_sop"] = workflow_sop
    bundle["model_preferences"] = state.get("model_preferences") or {}
    actions = list(state.get("actions") or [])
    actions.append("assemble_material")
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

    try:
        material_health = writing_tools.assess_material_health(bundle, task=task, chapter=chapter)
        bundle["material_health"] = material_health
        if material_health.get("level") == "warn":
            actions.append(f"material_health(warn,{len(material_health.get('warnings') or [])})")
    except Exception as exc:
        bundle["material_health"] = {"ok": False, "level": "warn", "error": f"{type(exc).__name__}: {exc}"}
        actions.append("material_health_failed")

    # 生成"provider 提问文件"（材料+规范+限制拼成可读 prompt，落盘到项目 输出/），供 provider 作答。
    try:
        from app.provider_prompt_file import write_request_file
        rf = write_request_file(task, bundle, chapter, message)
        bundle["request_text"] = rf.get("text") or message
        bundle["request_harness"] = rf.get("harness") or {}
        bundle["token_budget"] = rf.get("budget") or {}
        actions.append("build_request_file")
        if (rf.get("harness") or {}).get("level") in {"warn", "error"}:
            actions.append(f"prompt_harness({(rf.get('harness') or {}).get('level')})")
        if (rf.get("budget") or {}).get("level") in {"warn", "error"}:
            actions.append(f"budget({(rf.get('budget') or {}).get('level')})")
        if not rf.get("ok", True):
            issues = (rf.get("harness") or {}).get("issues") or []
            message_text = "；".join(item.get("message", "") for item in issues[:3]) or "provider 提问包未通过 harness。"
            raise writing_tools.WritingToolError(message_text)
        rf_path = rf.get("path", "")
        _flow_debug(
            f"draft_assemble novel={state.get('novel_id')} kind={state.get('project_kind')} "
            f"task={task} chapter={chapter} query_len={len(assembly_query)} "
            f"request_file={rf_path} prompt_len={len(bundle.get('request_text') or '')}"
        )
    except writing_tools.WritingToolError:
        raise
    except Exception:
        bundle["request_text"] = message
        rf_path = ""
    return {
        "bundle": bundle,
        "chapter": chapter,
        "workflow_sop": workflow_sop,
        "actions": actions,
        "request_file": rf_path,
        "request_harness": bundle.get("request_harness") or {},
        "token_budget": bundle.get("token_budget") or {},
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


def node_prompt_refine(state: WritingState) -> WritingState:
    """Short-film skill layer: rewrite user request into a professional provider prompt."""
    if state.get("project_kind") != SHORT_FILM_KIND:
        return {}
    if not (state.get("use_provider_source") and any((state.get("login_confirmed") or {}).values())):
        actions = list(state.get("actions") or [])
        actions.append("prompt_refine_skipped(no_provider)")
        return {"actions": actions}
    actions = list(state.get("actions") or [])
    try:
        from app.short_film_skills import refine_short_film_prompt
        refined = refine_short_film_prompt(
            novel_id=state.get("novel_id"),
            task=state.get("task", "screenplay"),
            chapter=state.get("chapter"),
            user_message=state.get("user_message", ""),
            bundle=state.get("bundle") or {},
            model_key=(state.get("model_preferences") or {}).get("writing"),
        )
        if refined.get("ok"):
            bundle = dict(state.get("bundle") or {})
            bundle["request_text"] = refined["text"]
            harness = check_request_text(
                project_kind=state.get("project_kind"),
                task=state.get("task", "screenplay"),
                request_text=refined["text"],
                workflow_sop=state.get("workflow_sop") or bundle.get("workflow_sop") or {},
            )
            budget = estimate_budget(prompt_text=refined["text"])
            bundle["request_harness"] = harness
            bundle["token_budget"] = budget
            actions.append("prompt_refine(short_film_skill)")
            if harness.get("level") in {"warn", "error"}:
                actions.append(f"prompt_harness({harness.get('level')})")
            if not harness.get("ok", True):
                issues = harness.get("issues") or []
                message_text = "；".join(item.get("message", "") for item in issues[:3]) or "短片专业提问未通过 harness。"
                raise writing_tools.WritingToolError(message_text)
            return {
                "refined_prompt": refined,
                "bundle": bundle,
                "actions": actions,
                "request_harness": harness,
                "token_budget": budget,
            }
        actions.append(f"prompt_refine_skipped({refined.get('reason') or refined.get('error') or 'failed'})")
        return {"refined_prompt": refined, "actions": actions}
    except Exception as exc:
        actions.append("prompt_refine_failed")
        return {"refined_prompt": {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, "actions": actions}


def node_generate(state: WritingState) -> WritingState:
    """正文主产出：provider 答案为主。

    - 有 provider 答案：prose → 五维提要+取最优融合成 1 篇；其他环节 → 直接用 provider 答案（择最长）。
    - 无 provider 答案（全失败/未开 provider）：
        · 创作模式+AI（use_provider_source）→ 标记 provider_failed，前端给"用API生成"兜底按钮。
        · 否则 → 按用户选择的“创作”模型做材料驱动生成。
    """
    bundle = state.get("bundle") or {}
    task = state.get("task", "prose")
    answers = state.get("provider_answers") or []
    actions = list(state.get("actions") or [])
    route = state.get("provider_route") or {}
    use_provider = bool(state.get("use_provider_source")) and route.get("decision") != "single_agent"

    if answers:
        project_kind = bundle.get("project_kind") or state.get("project_kind", "")
        if task == "prose" or project_kind == SHORT_FILM_KIND:
            out = _merge_provider_outputs(bundle, answers, actions, task=task)
            return {"draft": out["draft"], "merge_info": out["merge_info"],
                    "actions": actions, "iterations": state.get("iterations", 0) + 1,
                    "provider_failed": False}
        # 其他环节：直接采用 provider 答案（取最长的一篇作为草稿，用户再确认/改写）
        best = max(answers, key=lambda a: len(a.get("result") or ""))
        draft = best.get("result", "")
        actions.append(f"use_provider({best.get('name')})")
        return {"draft": draft, "actions": actions,
                "iterations": state.get("iterations", 0) + 1, "provider_failed": False}

    # 无 provider 答案
    if use_provider:
        actions.append("provider_failed")
        return {"draft": "", "provider_failed": True, "actions": actions,
                "iterations": state.get("iterations", 0) + 1}

    # 非 provider 场景：按用户选择的“创作”模型做材料驱动生成。
    feedback = ""
    mr = state.get("model_review") or {}
    pr = state.get("pre_review") or {}
    if mr and not mr.get("passed", True):
        feedback = review_feedback_text(mr)
    elif pr and pr.get("blocking_count", 0) > 0:
        feedback = writing_tools.pre_review_issues_text(pr.get("issues") or [])
    out = generate_prose(
        bundle, model_key=(state.get("model_preferences") or {}).get("writing"),
        provider_answers=[],
        revise_target=state.get("draft", "") if state.get("intent") == "revise" else "",
        review_feedback=feedback,
    )
    actions.append(f"generate({out.get('model')})")
    return {"draft": out.get("text", ""), "actions": actions,
            "iterations": state.get("iterations", 0) + 1, "provider_failed": False}


def _merge_provider_outputs(bundle: dict, answers: list[dict], actions: list[str],
                            task: str = "prose") -> dict:
    """provider 融合：逐篇提要评分 → 取最优融合成一篇。"""
    from app.prose_merge import digest_one, merge_drafts
    from app.provider_answer_review import analyze_provider_answers

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

    materials = bundle.get("materials") or {}
    project_kind = bundle.get("project_kind") or ""
    request_analysis = bundle.get("request_analysis") or {}
    brief = "\n".join(filter(None, [
        f"大纲：{materials.get('chapter_outline','')}",
        f"人物：{materials.get('character_profiles','')}",
        f"约束：{materials.get('constraints','')}",
        f"项目文档：{materials.get('project_docs','')}",
    ]))[:4000]
    technique_context: dict[str, Any] = {}
    try:
        from app.writing_techniques import technique_context_for_task

        technique_context = technique_context_for_task(
            query="\n".join(filter(None, [
                str(bundle.get("user_request") or bundle.get("request_text") or ""),
                str(request_analysis.get("generator_instruction") or ""),
                str(request_analysis.get("reason") or ""),
            ])),
            outline="\n".join(filter(None, [
                str(materials.get("chapter_outline") or ""),
                str(materials.get("outline_context") or ""),
            ])),
            project_kind=project_kind,
            task=task,
            model_key=((bundle.get("model_preferences") or {}).get("review")),
            max_lines=6,
        )
        if technique_context.get("text"):
            brief = "\n\n".join([brief, technique_context["text"]])[:5200]
            bundle["technique_context"] = technique_context
            actions.append(f"technique_context({technique_context.get('mode')},{len(technique_context.get('lines') or [])})")
    except Exception as exc:
        actions.append(f"technique_context_failed({type(exc).__name__})")
    spec = bundle.get("spec") or ""
    drafts = {a.get("name") or a.get("provider"): (a.get("result") or "") for a in answers}
    emit_stage("provider_consensus", "running", total=len(answers))
    provider_review = analyze_provider_answers(answers)
    emit_stage(
        "provider_consensus",
        "done",
        consensus=len(provider_review.get("consensus") or []),
        divergence=len(provider_review.get("divergences") or []),
        adoptable=len(provider_review.get("adoptable_points") or []),
    )
    actions.append(
        f"provider_review(consensus={len(provider_review.get('consensus') or [])},"
        f"divergence={len(provider_review.get('divergences') or [])})"
    )
    prefs = bundle.get("model_preferences") or {}
    review_model = prefs.get("review")
    writing_model = prefs.get("writing")
    digests = []
    total_drafts = len(drafts)
    emit_stage("provider_digest", "running", current=0, total=total_drafts)
    for idx, (name, prose) in enumerate(drafts.items(), start=1):
        emit_stage("provider_digest", "running", current=idx, total=total_drafts, provider=name)
        digests.append(
            digest_one(name, prose, brief, model_key=review_model, project_kind=project_kind, task=task)
        )
    emit_stage("provider_digest", "done", current=total_drafts, total=total_drafts)
    actions.append(f"digest({len(digests)})")
    emit_stage("provider_merge", "running", total=total_drafts)
    merged = merge_drafts(drafts, digests, brief, spec=spec, model_key=writing_model,
                          project_kind=project_kind, task=task, provider_review=provider_review)
    emit_stage("provider_merge", "done", model=merged.get("model"), used_fulltext=merged.get("used_fulltext"))
    actions.append(f"merge({merged.get('model')},fulltext={merged.get('used_fulltext')})")
    return {"draft": merged.get("text", ""),
            "merge_info": {"best_per_dimension": merged.get("best_per_dimension"),
                           "used_fulltext": merged.get("used_fulltext"),
                           "provider_review": provider_review,
                           "digests": digests,
                           "technique_context": technique_context}}


def node_pre_review(state: WritingState) -> WritingState:
    if state.get("project_kind") in {"short_film", "generic"}:
        actions = list(state.get("actions") or [])
        actions.append("pre_review_skipped(generic)")
        return {"pre_review": {"ok": True, "passed": True, "blocking_count": 0, "issues": []}, "actions": actions}
    bundle = state.get("bundle") or {}
    outline = (bundle.get("materials") or {}).get("chapter_outline", "") or ""
    pr = writing_tools.pre_review_text(state.get("draft", ""), outline=outline)
    actions = list(state.get("actions") or [])
    actions.append("pre_review_text")
    return {"pre_review": pr, "actions": actions}


def node_model_review(state: WritingState) -> WritingState:
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
        request_harness=state.get("request_harness") or (state.get("bundle") or {}).get("request_harness") or {},
        token_budget=state.get("token_budget") or (state.get("bundle") or {}).get("token_budget") or {},
        provider_route=state.get("provider_route") or {},
    )
    if strategy.get("mode") == "skip":
        actions = list(state.get("actions") or [])
        actions.append(f"model_review_skipped({strategy.get('reason')})")
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
        )
        actions.append(f"model_review({review.get('model')})")
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
    artifacts = {}
    if state.get("provider_answers"):
        try:
            artifacts["provider_answers"] = _save_provider_artifacts(
                state.get("novel_id"), task, state.get("provider_answers") or [],
                state.get("merge_info") or {},
            )
            actions.append("save_provider_artifacts")
        except Exception:
            pass
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
            "provider_failed": bool(state.get("provider_failed")),
            "merge_info": state.get("merge_info") or {},
            "request_file": state.get("request_file", ""),
            "refined_prompt": state.get("refined_prompt") or {},
            "request_harness": state.get("request_harness") or (state.get("bundle") or {}).get("request_harness") or {},
            "token_budget": state.get("token_budget") or (state.get("bundle") or {}).get("token_budget") or {},
            "provider_route": state.get("provider_route") or {},
            "need_audit": state.get("need_audit") or {},
            "material_health": (state.get("bundle") or {}).get("material_health") or {},
            "request_analysis": state.get("request_analysis") or {},
            "pending_intent": state.get("pending_intent") or {},
            "provider_answers": state.get("provider_answers") or [],
            "artifacts": artifacts,
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


def _save_provider_artifacts(novel_id: str | None, task: str, answers: list[dict], merge_info: dict) -> dict:
    import json
    import os
    from datetime import datetime

    from app.config import ROOT
    from app.novel_context import normalize_novel_id

    nid = normalize_novel_id(novel_id)
    out_dir = outputs_dir(nid)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    data = {
        "novel_id": nid,
        "task": task,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "provider_answers": answers,
        "merge_info": merge_info,
    }
    path = out_dir / f"{task}_provider_answers_{stamp}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        rel = str(path)
    return {"ok": True, "path": rel, "count": len(answers)}


def _after_pre_review(state: WritingState) -> str:
    """预审查门禁：blocking>0 且未到回环上限 → 回 generate 重新生成；否则进模型审查。

    provider 全失败（draft 空）→ 直接 finalize，由前端给"用API生成"兜底按钮。
    """
    if state.get("provider_failed") or not (state.get("draft") or "").strip():
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


def _after_provider_fanout(state: WritingState) -> str:
    if state.get("provider_answers"):
        return "confirm"
    return "generate"


def _route(state: WritingState) -> str:
    return state.get("intent", "search")


def _draft_start(state: WritingState) -> str:
    """首问走材料组装；AI 固定会话续问可显式跳过材料组装。"""
    if (
        state.get("skip_material_assemble")
        and state.get("use_provider_source")
        and any((state.get("login_confirmed") or {}).values())
    ):
        return "context_followup"
    return "assemble"


def _provider_route_next(state: WritingState) -> str:
    """Route after materials/prompt refinement using information boundaries."""
    route = state.get("provider_route") or {}
    if route.get("decision") == "fanout":
        return "with_provider"
    return "direct"


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
    graph.add_node("context_followup", node_context_followup)
    graph.add_node("provider_confirm_gate", node_provider_confirm_gate)
    graph.add_node("provider_route", node_provider_route)
    graph.add_node("prompt_refine", node_prompt_refine)
    graph.add_node("provider_fanout", node_provider_fanout)
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

    # draft/revise：首问组装材料；显式续问时跳过组装，沿用 provider 固定会话上下文。
    graph.add_edge("draft_entry", "need_audit")
    graph.add_conditional_edges(
        "need_audit", _draft_start,
        {"assemble": "draft_assemble", "context_followup": "context_followup"},
    )
    graph.add_edge("context_followup", "provider_route")
    graph.add_edge("draft_assemble", "prompt_refine")
    graph.add_edge("prompt_refine", "provider_route")
    graph.add_conditional_edges(
        "provider_route", _provider_route_next,
        {"with_provider": "provider_fanout", "direct": "generate"},
    )
    graph.add_conditional_edges(
        "provider_fanout", _after_provider_fanout,
        {"confirm": "provider_confirm_gate", "generate": "generate"},
    )
    graph.add_edge("provider_confirm_gate", END)
    # generate 后两道门禁回环
    graph.add_edge("generate", "pre_review")
    # 审查回环回到 generate（重新融合/重新生成），不回 draft_assemble（避免重抓 provider）。
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
            "build_index": "构建索引",
            "review": "章节审查",
            "assemble": "材料组装",
            "search": "参考检索",
            "draft_entry": "创作入口",
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
    group_order = ["系统边界", "入口理解", "路由分支", "创作主线", "网页模型", "审查回环", "定稿确认", "其他"]
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
