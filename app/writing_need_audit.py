from __future__ import annotations

import re
from typing import Any

from app.project_kinds import SHORT_FILM_KIND, STRONG_NOVEL_KIND


_TASK_KEYWORDS = {
    "logline": ["概念", "logline", "一句话", "主题", "命题"],
    "character": ["角色", "人物", "主角", "反派", "关系", "弧光"],
    "beat_sheet": ["节拍", "情节", "大纲", "结构", "转折", "反转"],
    "screenplay": ["剧本", "脚本", "场景标题", "对白", "分场"],
    "shot_list": ["分镜", "镜头", "景别", "机位", "画面", "生图"],
    "fix": ["修订", "修改", "修复", "润色", "问题", "调整"],
    "prose": ["正文", "章节", "扩写", "写第"],
    "outline": ["大纲", "梗概", "结构"],
}

_EMPTY_VERBS = ["优化", "完善", "提升", "丰富", "加强", "高级", "更好", "专业一点"]
_DELIVERABLE_HINTS = {
    "logline": "概念开发材料",
    "character": "角色材料",
    "beat_sheet": "节拍/大纲材料",
    "screenplay": "正式剧本",
    "shot_list": "分镜镜头表",
    "fix": "修订稿",
    "prose": "小说正文",
    "outline": "小说大纲",
}


def audit_need(
    *,
    message: str,
    project_kind: str | None,
    task: str | None,
    chapter: int | None = None,
    use_provider_source: bool = False,
) -> dict[str, Any]:
    """Deterministic Need Audit before material assembly.

    The goal is not to ask another model for interpretation. It extracts the
    requested deliverable, catches ambiguity, and records routing hints so the
    later graph run is auditable.
    """
    text = (message or "").strip()
    kind = project_kind or ""
    selected_task = task or ""
    suggested_task = _suggest_task(text, kind) or selected_task
    risks: list[dict[str, Any]] = []
    missing: list[str] = []

    if not text:
        risks.append(_risk("blocker", "empty_need", "用户需求为空，无法审计交付物。"))
    if any(word in text for word in _EMPTY_VERBS) and len(text) < 80:
        risks.append(_risk("warn", "empty_verb", "需求含空泛动词但缺少具体目标或验收标准。"))

    if suggested_task and selected_task and suggested_task != selected_task:
        risks.append(_risk(
            "warn",
            "task_mismatch",
            f"用户措辞更像“{_DELIVERABLE_HINTS.get(suggested_task, suggested_task)}”，当前按钮是“{_DELIVERABLE_HINTS.get(selected_task, selected_task)}”。",
            {"suggested_task": suggested_task, "selected_task": selected_task},
        ))

    if kind == SHORT_FILM_KIND:
        _audit_short_film(text, selected_task, suggested_task, missing, risks)
    elif kind == STRONG_NOVEL_KIND:
        _audit_novel(text, selected_task, chapter, missing, risks)

    has_blocker = any(item["severity"] == "blocker" for item in risks)
    provider_recommended = bool(use_provider_source and suggested_task in {"logline", "character", "beat_sheet", "screenplay", "prose", "outline"})
    return {
        "ok": not has_blocker,
        "level": "error" if has_blocker else ("warn" if risks else "ok"),
        "project_kind": kind,
        "selected_task": selected_task,
        "suggested_task": suggested_task,
        "deliverable": _DELIVERABLE_HINTS.get(suggested_task or selected_task, suggested_task or selected_task or "未识别"),
        "chapter": chapter,
        "provider_recommended": provider_recommended,
        "risks": risks,
        "missing": missing,
        "intent_cards": _intent_cards(text, suggested_task or selected_task),
    }


def _suggest_task(text: str, project_kind: str) -> str:
    if not text:
        return ""
    scores: dict[str, int] = {}
    for task, words in _TASK_KEYWORDS.items():
        scores[task] = sum(1 for word in words if word.lower() in text.lower())
    if project_kind == SHORT_FILM_KIND and scores.get("prose", 0) and "剧本" in text:
        scores["screenplay"] = scores.get("screenplay", 0) + 2
    best = max(scores.items(), key=lambda item: item[1])
    return best[0] if best[1] > 0 else ""


def _audit_short_film(text: str, selected_task: str, suggested_task: str, missing: list[str], risks: list[dict[str, Any]]) -> None:
    target = suggested_task or selected_task
    if target == "logline" and re.search(r"不超过\s*50\s*字|一句话故事|只输出", text):
        risks.append(_risk("warn", "concept_word_limit_requested", "用户需求含单句/字数压缩倾向；若目标是短片开发，应保留结构化概念材料。"))
    if target == "screenplay" and not any(mark in text for mark in ["时长", "分钟", "场景", "人物", "主题"]):
        missing.extend(["短片时长", "主要人物", "主题或核心冲突"])
    if target == "shot_list" and not any(mark in text for mark in ["剧本", "节拍", "画面", "镜头"]):
        missing.append("可转化为镜头的剧本或节拍材料")


def _audit_novel(text: str, selected_task: str, chapter: int | None, missing: list[str], risks: list[dict[str, Any]]) -> None:
    if selected_task in {"prose", "beat_sheet", "expansion"} and not chapter:
        missing.append("章节号")
        risks.append(_risk("warn", "chapter_missing", "章节型任务未提供章节号，可能影响材料召回。"))
    if selected_task == "fix" and not any(mark in text for mark in ["问题", "修复", "审查", "改"]):
        missing.append("明确修复问题")


def _intent_cards(text: str, task: str) -> list[dict[str, str]]:
    cards = []
    if task:
        cards.append({"type": "deliverable", "text": _DELIVERABLE_HINTS.get(task, task)})
    if text:
        cards.append({"type": "raw_need", "text": text[:180]})
    return cards


def _risk(severity: str, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    item = {"severity": severity, "code": code, "message": message}
    if details:
        item["details"] = details
    return item
