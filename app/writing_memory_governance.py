from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from app.chat_history import load_by_track
from app.chapter_summary import relevant_summaries, summary_file
from app.novel_context import normalize_novel_id
from app.writing_invocations import list_recent_invocations
from app.writing_lessons import list_lessons
from app.writing_memory import load_settings


AUTHORITY = {
    "confirmed_setting": 80,
    "chapter_summary": 70,
    "lesson": 65,
    "invocation": 35,
    "chat": 20,
}


def memory_governance_report(novel_id: str, limit: int = 50) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    items = _collect_items(nid, limit=limit)
    conflicts = _detect_conflicts(items)
    promotions = _promotion_candidates(nid, items, limit=limit)
    return {
        "ok": True,
        "novel_id": nid,
        "items": len(items),
        "conflicts": conflicts,
        "promotion_candidates": promotions,
        "summary": {
            "conflicts": len(conflicts),
            "promotion_candidates": len(promotions),
            "high_authority_items": sum(1 for item in items if item["authority"] >= 65),
        },
    }


def promote_memory_candidate(
    novel_id: str,
    text: str,
    *,
    title: str = "",
    source: str = "",
    task: str = "",
    target: str = "project_skill",
    track: str = "create",
) -> dict[str, Any]:
    """Promote a human-confirmed low-authority note into reusable project memory."""
    nid = normalize_novel_id(novel_id)
    clean_text = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean_text) < 8:
        raise ValueError("晋级内容太短")
    clean_title = (title or _promotion_title(clean_text)).strip()[:80]
    if target == "project_skill":
        from app.writing_lessons import append_lesson_skill

        markdown = "\n".join(filter(None, [
            f"# {clean_title}",
            "",
            f"- 来源：{source or 'memory_governance'}",
            f"- 晋级时间：{datetime.now().isoformat(timespec='seconds')}",
            f"- 规则：{clean_text}",
            "",
        ]))
        skill = append_lesson_skill(nid, clean_title, markdown, task=task or "通用", source_path=source or "memory_governance")
        return {
            "ok": True,
            "novel_id": nid,
            "target": target,
            "authority": AUTHORITY["lesson"],
            "skill": skill,
        }
    if target == "llm_wiki":
        from app.writing_wiki import adopt_wiki_entry

        wiki = adopt_wiki_entry(
            nid,
            title=clean_title,
            content=clean_text,
            category="consensus",
            source=source or "memory_governance",
            task=task or "all",
            authority="human_confirmed",
        )
        return {
            "ok": True,
            "novel_id": nid,
            "target": target,
            "authority": 90,
            "wiki": wiki,
        }
    if target == "confirmed_setting":
        from app.writing_memory import save_setting

        key = f"promoted:{hashlib.sha256(clean_text.encode('utf-8')).hexdigest()[:12]}"
        save_setting(track, key, {
            "title": clean_title,
            "content": clean_text,
            "source": source or "memory_governance",
            "promoted_at": datetime.now().isoformat(timespec="seconds"),
            "authority": "human_confirmed",
        }, project=nid)
        return {
            "ok": True,
            "novel_id": nid,
            "target": target,
            "authority": AUTHORITY["confirmed_setting"],
            "key": key,
        }
    raise ValueError("target 仅支持 project_skill、confirmed_setting 或 llm_wiki")


def _collect_items(novel_id: str, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for track in ("create", "normal"):
        for setting in load_settings(track, project=novel_id):
            value = setting.get("value") or {}
            text = value.get("content") if isinstance(value, dict) else str(value)
            items.append(_item("confirmed_setting", setting.get("key", ""), text or "", AUTHORITY["confirmed_setting"]))

    try:
        path = summary_file(novel_id)
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            for block in re.findall(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL):
                items.append(_item("chapter_summary", "chapter_summary", block, AUTHORITY["chapter_summary"]))
    except Exception:
        pass

    try:
        for lesson in list_lessons(limit=limit).get("items") or []:
            items.append(_item("lesson", lesson.get("id", ""), lesson.get("title", ""), AUTHORITY["lesson"]))
    except Exception:
        pass

    for record in list_recent_invocations(novel_id, limit=min(limit, 20)):
        preview = record.get("user_message_preview", "")
        if preview:
            items.append(_item("invocation", record.get("id", ""), preview, AUTHORITY["invocation"]))

    try:
        for msg in load_by_track("create", limit=min(limit, 50), novel_id=novel_id).get("messages") or []:
            if msg.get("role") == "user" and msg.get("text"):
                items.append(_item("chat", str(msg.get("seq", "")), msg.get("text", ""), AUTHORITY["chat"]))
    except Exception:
        pass
    return items


def _detect_conflicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts = []
    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        for key in _topic_keys(item["text"]):
            by_key.setdefault(key, []).append(item)
    for key, bucket in by_key.items():
        if len(bucket) < 2:
            continue
        pos = [item for item in bucket if _polarity(item["text"]) > 0]
        neg = [item for item in bucket if _polarity(item["text"]) < 0]
        if pos and neg:
            conflicts.append({
                "topic": key,
                "level": "warn",
                "message": "同一主题存在肯定/否定或允许/禁止类表述，需要人工裁决。",
                "positive": _brief(pos[0]),
                "negative": _brief(neg[0]),
            })
    return conflicts[:20]


def _promotion_candidates(novel_id: str, items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    existing_hashes = {_fingerprint(item["text"]) for item in items if item["authority"] >= 65}
    candidates = []
    for item in items:
        if item["authority"] >= 65:
            continue
        text = item["text"].strip()
        if not _looks_like_rule(text):
            continue
        if _fingerprint(text) in existing_hashes:
            continue
        candidates.append({
            "id": f"PROMOTE-{item['source']}-{item['key']}",
            "source": item["source"],
            "authority": item["authority"],
            "target": "llm_wiki",
            "reason": "低权威聊天/任务里出现可复用规则，建议人工确认后写入 LLM Wiki 稳定共识。",
            "text": text[:500],
        })
    return candidates[:limit]


def _promotion_title(text: str) -> str:
    terms = _topic_keys(text)
    if terms:
        return "记忆晋级：" + " / ".join(terms[:3])
    return "记忆晋级"


def _item(source: str, key: str, text: str, authority: int) -> dict[str, Any]:
    return {"source": source, "key": key, "text": str(text or ""), "authority": authority}


def _topic_keys(text: str) -> list[str]:
    terms = re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z0-9_]{3,}", text or "")
    stop = {"这个", "我们", "用户", "输出", "项目", "任务", "材料", "需要", "不能", "不要", "必须"}
    return [term.lower() for term in terms if term not in stop][:12]


def _polarity(text: str) -> int:
    negative = ["不要", "禁止", "不能", "不得", "避免", "不应该", "严禁"]
    positive = ["必须", "需要", "应该", "允许", "保留", "采用", "确认"]
    neg = sum(1 for token in negative if token in text)
    pos = sum(1 for token in positive if token in text)
    return (1 if pos else 0) - (1 if neg else 0)


def _looks_like_rule(text: str) -> bool:
    markers = ["必须", "不要", "不能", "禁止", "以后", "记住", "设定", "规则", "确认", "采用"]
    return len(text) >= 12 and any(marker in text for marker in markers)


def _fingerprint(text: str) -> str:
    normalized = "".join(_topic_keys(text))[:120]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": item["source"],
        "key": item["key"],
        "authority": item["authority"],
        "text": item["text"][:220],
    }
