from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from app.novel_context import WRITING_ROOT
from app.project_kinds import SHORT_FILM_KIND, STRONG_NOVEL_KIND


METHODOLOGY_KB_PATH = WRITING_ROOT / "data" / "novel_creation_method_knowledge.json"


SHORT_FILM_METHOD_LAWS = {
    "logline": [
        "短片前置定位：先锁定目标观众、片长体量、核心命题、主角欲望和结尾余味，避免概念只剩一句口号。",
        "可拍性约束：概念必须能转译为场景、动作、道具、声音和选择代价，不把抽象主题当作交付物。",
    ],
    "character": [
        "可表演人物：人物卡要落到欲望、阻碍、秘密、关系压力、说话方式和可见行为。",
        "关系驱动：每个重要关系至少包含索取、隐瞒、拒绝或补偿之一，便于后续写成戏。",
    ],
    "beat_sheet": [
        "节拍功能：每个节拍必须改变信息、关系、风险或选择，不写重复状态。",
        "低成本推进：短片节拍优先用空间变化、声音线索、道具和停顿完成压力升级。",
    ],
    "screenplay": [
        "画面优先：剧本正文优先写动作、距离、停顿、视线、声音，少用解释性心理旁白。",
        "对白潜台词：对白表层信息和真实需求错开，每句至少推动信息、关系或冲突之一。",
    ],
    "shot_list": [
        "镜头任务：每个镜头要承担信息、情绪、转折或节奏功能，不能只是漂亮描述。",
        "视听闭环：分镜同时交代画面、声音和备注，保证后续能被执行。",
    ],
}


@lru_cache(maxsize=1)
def load_methodology_knowledge() -> dict[str, Any]:
    try:
        data = json.loads(METHODOLOGY_KB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}
    return data if isinstance(data, dict) else {"items": []}


def methodology_context_for_task(
    *,
    query: str = "",
    project_kind: str = "",
    task: str = "",
    creative_stage: str = "",
    max_lines: int = 6,
) -> dict[str, Any]:
    """Return abstract creative-method laws for the current creation stage.

    This module is intentionally project-agnostic. It never returns source
    excerpts, prompt templates, imported novel text, or project-specific nouns.
    """
    kind = str(project_kind or "")
    task_key = str(task or "").strip().lower()
    stage_key = str(creative_stage or "").strip().lower()
    if kind == SHORT_FILM_KIND:
        lines = _short_film_laws(task_key, query, max_lines)
        mode = "short_film_overlay"
    elif kind == STRONG_NOVEL_KIND:
        lines = _recall_method_laws(query=query, task=task_key, creative_stage=stage_key, limit=max_lines)
        mode = "novel_method_recall"
    else:
        lines = _generic_laws(max_lines)
        mode = "generic_overlay"
    lines = _clean_lines(lines, max_lines=max_lines)
    return {
        "ok": bool(lines),
        "project_kind": kind,
        "task": task_key,
        "creative_stage": stage_key,
        "mode": mode,
        "lines": lines,
        "text": format_methodology_context(lines, project_kind=kind, task=task_key, creative_stage=stage_key),
    }


def creative_preflight(
    *,
    query: str = "",
    project_kind: str = "",
    task: str = "",
    creative_stage: str = "",
    material_signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build non-blocking preflight checks before generation.

    The result is a control surface for generation/review. It warns and guides;
    it does not block the original writing flow when materials are missing.
    """
    kind = str(project_kind or "")
    task_key = str(task or "").strip().lower()
    stage_key = str(creative_stage or "").strip().lower()
    signals = material_signals or {}
    checks: list[dict[str, Any]] = []

    if kind == STRONG_NOVEL_KIND:
        if stage_key in {"concept", "setting", ""} or task_key in {"logline", "brief", "setting"}:
            checks.extend([
                _check("target_reader", "先明确平台/读者/题材承诺，避免只写抽象灵感。"),
                _check("core_promise", "概念稿必须包含主角欲望、阻碍、核心命题和故事承诺。"),
            ])
        if stage_key in {"outline", "plot", "prose"} or task_key in {"outline", "beat_sheet", "prose"}:
            checks.extend([
                _check("motivation_chain", "逐场检查人物动机、信息边界、收益/代价是否成立。"),
                _check("chapter_function", "先判断章节功能：布局、战斗/冲突、过渡、恢复或信息揭示。"),
                _check("hook_ledger", "确认本章新增、推进或回收的钩子，归档后更新伏笔账本。"),
            ])
        if task_key in {"prose", "expansion", "fix"}:
            checks.extend([
                _check("reader_experience", "正文要交代爽点/压力/释放/余味，避免只有设定说明。"),
                _check("anti_ai_flavor", "少用空泛环境铺垫和机械总结，多用具体动作、对白、选择。"),
            ])
            if not signals.get("creative_state"):
                checks.append(_check("missing_state_card", "未读到当前状态卡，连续性检查降级为项目材料与用户请求。", level="warn"))
            if not signals.get("hook_ledger"):
                checks.append(_check("missing_hook_ledger", "未读到伏笔账本，伏笔回收需人工重点复核。", level="warn"))
    elif kind == SHORT_FILM_KIND:
        checks.extend([
            _check("film_deliverable", "确认本轮交付是概念、角色、节拍、剧本还是分镜，避免混写。"),
            _check("shootable_action", "输出必须能落成画面、动作、对白、声音或镜头任务。"),
        ])
        if task_key in {"screenplay", "prose"}:
            checks.append(_check("screenplay_boundary", "剧本阶段不写小说式说明，优先保留可拍摄内容。"))
    else:
        checks.append(_check("generic_boundary", "通用项目只整理用户目标所需材料，不强套小说或短片流程。"))

    if query and any(word in query for word in ["历史", "军事", "真实", "年代", "考据", "史实"]):
        checks.append(_check("research_needed", "涉及史实/行业/军事/年代细节时，需要检索或明确事实来源后再写。", level="warn"))

    text = format_preflight(checks)
    return {
        "ok": True,
        "project_kind": kind,
        "task": task_key,
        "creative_stage": stage_key,
        "level": "warn" if any(item.get("level") == "warn" for item in checks) else "ok",
        "checks": checks,
        "text": text,
    }


def format_methodology_context(lines: list[str], *, project_kind: str = "", task: str = "", creative_stage: str = "") -> str:
    if not lines:
        return ""
    if project_kind == SHORT_FILM_KIND:
        heading = "## 创作方法论知识库：短片流程法则"
    elif project_kind == STRONG_NOVEL_KIND:
        heading = "## 创作方法论知识库：小说阶段法则"
    else:
        heading = "## 创作方法论知识库：通用组织法则"
    meta = " / ".join(part for part in [creative_stage, task] if part)
    body = "\n".join(f"- {line}" for line in lines[:8])
    guard = "使用要求：只作为流程控制和验收检查，不要把方法术语、来源文档或分析说明写进正文定稿。"
    return "\n".join(part for part in [heading + (f"（{meta}）" if meta else ""), body, guard] if part)


def format_preflight(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return ""
    lines = ["## 创作前置检查：本轮必须自检的边界"]
    for item in checks[:10]:
        prefix = "警告" if item.get("level") == "warn" else "检查"
        lines.append(f"- {prefix}｜{item.get('message')}")
    lines.append("使用要求：这些是生成前检查点，不要作为正文内容输出。")
    return "\n".join(lines)


def _recall_method_laws(*, query: str, task: str, creative_stage: str, limit: int) -> list[str]:
    kb = load_methodology_knowledge()
    terms = set(_terms(" ".join([query, task, creative_stage])))
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in kb.get("items") or []:
        if not isinstance(item, dict):
            continue
        stages = [str(v).lower() for v in item.get("stages") or []]
        tasks = [str(v).lower() for v in item.get("tasks") or []]
        haystack = " ".join([
            str(item.get("name") or ""),
            str(item.get("summary") or ""),
            " ".join(item.get("keywords") or []),
            " ".join(stages),
            " ".join(tasks),
        ])
        overlap = len(terms & set(_terms(haystack)))
        stage_bonus = 4 if creative_stage and creative_stage in stages else 0
        task_bonus = 4 if task and task in tasks else 0
        score = overlap + stage_bonus + task_bonus
        if score > 0:
            scored.append((float(score), item))
    scored.sort(key=lambda pair: (pair[0], str(pair[1].get("id") or "")), reverse=True)
    lines: list[str] = []
    seen: set[str] = set()
    for _score, item in scored:
        line = str(item.get("prompt_law") or item.get("summary") or "").strip()
        name = str(item.get("name") or "").strip()
        if name and line and not line.startswith(name):
            line = f"{name}：{line}"
        if not line or line in seen:
            continue
        seen.add(line)
        lines.append(line)
        if len(lines) >= limit:
            break
    if lines:
        return lines
    return _generic_novel_laws(limit)


def _short_film_laws(task: str, query: str, limit: int) -> list[str]:
    lines = list(SHORT_FILM_METHOD_LAWS.get(task) or [])
    if len(lines) < limit:
        for values in SHORT_FILM_METHOD_LAWS.values():
            for line in values:
                if line not in lines:
                    lines.append(line)
                if len(lines) >= limit:
                    return lines
    return lines[:limit]


def _generic_novel_laws(limit: int) -> list[str]:
    return [
        "目标定位：先判断平台/读者/题材承诺，再决定设定、大纲和正文的展开粒度。",
        "阶段边界：概念写故事承诺，设定写规则边界，大纲写事件骨架，正文写场景行动。",
        "动机链自检：每个关键行为都要有欲望、信息边界、收益和代价，不用作者解释替代因果。",
        "状态卡优先：续写前先读当前状态和未回收钩子，避免机械通读大量旧章节。",
    ][:limit]


def _generic_laws(limit: int) -> list[str]:
    return [
        "目标先行：先确认用户要整理、扩展、回答还是沉淀，不自动升级为创作流程。",
        "材料边界：只组装与本轮问题直接相关的项目材料，避免把历史资料全部塞入上下文。",
        "可落盘：输出要能进入对应项目文件、草稿或知识库，避免只有过程说明。",
    ][:limit]


def _clean_lines(lines: list[str], *, max_lines: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = re.sub(r"\s+", " ", str(line or "")).strip(" -\t")
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if len(cleaned) >= max_lines:
            break
    return cleaned


def _terms(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", text or "")


def _check(code: str, message: str, *, level: str = "info") -> dict[str, Any]:
    return {"code": code, "level": level, "message": message}
