from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.novel_context import normalize_novel_id
from app.project_wiki import upsert_project_wiki_entry


def record_request_route_index(
    *,
    novel_id: str | None,
    invocation_id: str | None,
    track: str,
    message: str,
    analysis: dict[str, Any],
    pending_intent: dict[str, Any] | None = None,
    project_kind: str | None = "",
) -> dict[str, Any]:
    """Persist the current request route facts into project Wiki.

    This entry is written immediately after LLM/fallback intent analysis, before
    material assembly. It is intentionally compact and upserted by invocation so
    refresh/retry does not grow unbounded Wiki files.
    """
    nid = normalize_novel_id(novel_id)
    task = str((analysis or {}).get("task") or "generic")
    chapter = _chapter((analysis or {}).get("target_chapter"))
    entry_id = _route_entry_id(invocation_id, task, chapter, message)
    related_files = (pending_intent or {}).get("related_files") or (analysis or {}).get("related_files") or []
    content = _json_block({
        "type": "request_route_index",
        "recorded_at": _now(),
        "track": track,
        "project_kind": project_kind or "",
        "invocation_id": invocation_id or "",
        "message": _clip(message, 800),
        "route": {
            "source": analysis.get("source") or "",
            "intent": analysis.get("intent") or "",
            "task": task,
            "creative_stage": analysis.get("creative_stage") or "",
            "creative_stage_label": analysis.get("creative_stage_label") or "",
            "flow_entry": analysis.get("flow_entry") or "",
            "flow_complexity": analysis.get("flow_complexity") or "",
            "target_chapter": chapter,
            "context_chapters": analysis.get("context_chapters") or [],
            "node_flow": analysis.get("node_flow") or [],
        },
        "materials": {
            "affected_materials": analysis.get("affected_materials") or [],
            "affected_files": analysis.get("affected_files") or [],
            "related_files": related_files,
            "prose_locations": analysis.get("prose_locations") or [],
            "involved_characters": analysis.get("involved_characters") or [],
            "plot_points": analysis.get("plot_points") or [],
            "target_sections": analysis.get("target_sections") or [],
        },
        "reason": analysis.get("reason") or analysis.get("error") or "",
        "stage_conflict": analysis.get("stage_conflict") or {},
        "pending_intent_id": (pending_intent or {}).get("id", ""),
    })
    return upsert_project_wiki_entry(
        nid,
        entry_id=entry_id,
        title="本轮路由索引：" + _task_title(task, chapter),
        content=content,
        category="route",
        source=f"request_analyze:{invocation_id or task}",
        task=task,
        tags=["路由索引", "LLM意图分析", task, *( [f"第{chapter}章"] if chapter else [] )],
    )


def record_archive_summary(
    *,
    novel_id: str | None,
    invocation_id: str | None,
    track: str,
    task: str,
    chapter: int | None,
    result: dict[str, Any],
    request_analysis: dict[str, Any] | None = None,
    content: str | None = "",
) -> dict[str, Any]:
    """Persist archive completion facts and the best available summary."""
    nid = normalize_novel_id(novel_id)
    task_key = str(task or "generic")
    ch = _chapter(chapter)
    summary = _chapter_summary(nid, ch) if task_key == "prose" and ch else {}
    entry_id = _archive_entry_id(invocation_id, task_key, ch, result)
    content_text = str(content or "")
    wiki_content = _json_block({
        "type": "archive_summary",
        "recorded_at": _now(),
        "track": track,
        "invocation_id": invocation_id or "",
        "task": task_key,
        "chapter": ch,
        "archive": {
            "ok": bool(result.get("ok")),
            "file": result.get("file") or ((result.get("artifact") or {}).get("file") if isinstance(result.get("artifact"), dict) else ""),
            "backup": result.get("backup") or "",
            "snapshot": result.get("snapshot") or "",
            "overwritten": bool(result.get("overwritten")),
            "chapter_title": result.get("chapter_title") or "",
            "title_source": result.get("title_source") or "",
            "title_resolution": result.get("title_resolution") or {},
        },
        "request_route": {
            "source": (request_analysis or {}).get("source") or "",
            "creative_stage": (request_analysis or {}).get("creative_stage") or "",
            "creative_stage_label": (request_analysis or {}).get("creative_stage_label") or "",
            "flow_complexity": (request_analysis or {}).get("flow_complexity") or "",
            "affected_files": (request_analysis or {}).get("affected_files") or [],
            "related_files": (request_analysis or {}).get("related_files") or [],
            "prose_locations": (request_analysis or {}).get("prose_locations") or [],
        },
        "summary": summary,
        "content_digest": {
            "chars": len(content_text),
            "preview": _clip(content_text, 800),
        },
    })
    return upsert_project_wiki_entry(
        nid,
        entry_id=entry_id,
        title="归档摘要：" + _task_title(task_key, ch),
        content=wiki_content,
        category="state",
        source=f"archive:{invocation_id or task_key}",
        task=task_key,
        tags=["归档摘要", "归档完成", task_key, *( [f"第{ch}章"] if ch else [] )],
    )


def _chapter_summary(novel_id: str, chapter: int | None) -> dict[str, Any]:
    if not chapter:
        return {}
    try:
        from app.chapter_summary import _load_all

        return (_load_all(novel_id) or {}).get(str(chapter)) or {}
    except Exception:
        return {}


def _route_entry_id(invocation_id: str | None, task: str, chapter: int | None, message: str) -> str:
    if invocation_id:
        return f"route-{_safe_id(invocation_id)}"
    return f"route-{task}-{chapter or 'nounit'}-{_hash(message or task)}"


def _archive_entry_id(invocation_id: str | None, task: str, chapter: int | None, result: dict[str, Any]) -> str:
    if invocation_id:
        return f"archive-{_safe_id(invocation_id)}"
    seed = "|".join([task, str(chapter or "nounit"), str(result.get("file") or result.get("snapshot") or "")])
    return f"archive-{task}-{chapter or 'nounit'}-{_hash(seed)}"


def _task_title(task: str, chapter: int | None) -> str:
    return f"{task}｜第{chapter}章" if chapter else task


def _chapter(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _json_block(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[:limit].rstrip() + f"\n（已截断，原文 {len(value)} 字）"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _safe_id(value: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(value or "").strip())
    return clean[:80] or _hash(value)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
