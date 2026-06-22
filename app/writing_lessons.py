from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import WRITING_ROOT, normalize_novel_id
from app.writing_invocations import get_invocation, list_recent_invocations


LESSONS_ROOT = WRITING_ROOT / "lessons"


def list_lessons(limit: int = 50) -> dict[str, Any]:
    LESSONS_ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(LESSONS_ROOT.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True):
        text = path.read_text(encoding="utf-8", errors="replace")
        items.append({
            "id": path.stem,
            "title": _title(text, path.stem),
            "path": _rel(path),
            "chars": len(text),
            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
            "updated_at": path.stat().st_mtime,
        })
        if len(items) >= limit:
            break
    return {"ok": True, "root": _rel(LESSONS_ROOT), "items": items}


def adopt_lesson(
    title: str,
    draft_markdown: str,
    source_invocation_id: str = "",
    *,
    novel_id: str | None = None,
    task: str = "",
    apply_to_skill: bool = True,
) -> dict[str, Any]:
    """Persist a human-approved lesson into the project lessons registry."""
    clean_title = _clean_title(title)
    text = (draft_markdown or "").strip()
    if not text:
        raise ValueError("lesson 内容不能为空")
    if not text.lstrip().startswith("#"):
        text = f"# {clean_title}\n\n{text}"
    if source_invocation_id and "来源 invocation" not in text:
        text = f"{text.rstrip()}\n\n## 来源\n- invocation：{source_invocation_id}\n"

    LESSONS_ROOT.mkdir(parents=True, exist_ok=True)
    digest = _lesson_digest(text)
    stamp = datetime.now().strftime("%Y%m%d")
    path = LESSONS_ROOT / f"LL-{stamp}-{_slug(clean_title)}-{digest}.md"
    existing_path = _existing_lesson_path(digest)
    if existing_path:
        return {
            "ok": True,
            "created": False,
            "already_adopted": True,
            "id": existing_path.stem,
            "path": _rel(existing_path),
            "hash": digest,
        }
    if path.exists():
        return {
            "ok": True,
            "created": False,
            "already_adopted": True,
            "id": path.stem,
            "path": _rel(path),
            "hash": digest,
        }
    path.write_text(text.rstrip() + "\n", encoding="utf-8", newline="")
    result = {
        "ok": True,
        "created": True,
        "id": path.stem,
        "title": clean_title,
        "path": _rel(path),
        "hash": digest,
        "chars": path.stat().st_size,
    }
    if novel_id and apply_to_skill:
        result["skill"] = append_lesson_skill(novel_id, clean_title, text, task=task, source_path=_rel(path))
    return result


def append_lesson_skill(novel_id: str, title: str, markdown: str, task: str = "", source_path: str = "") -> dict[str, Any]:
    """Append an adopted lesson to the matching public skill suite if reusable, otherwise project skills."""
    from app.project_kinds import SHORT_FILM_KIND, STRONG_NOVEL_KIND, project_kind

    kind = project_kind(novel_id)
    if kind == SHORT_FILM_KIND:
        from app.short_film_skill_store import append_lesson_to_skill_store

        return append_lesson_to_skill_store(novel_id, title, markdown, task=task, source_path=source_path)
    if kind == STRONG_NOVEL_KIND:
        from app.novel_skills import append_lesson_to_skill_store

        return append_lesson_to_skill_store(novel_id, title, markdown, task=task, source_path=source_path)

    from app.novel_skills import append_project_lesson_skill

    return append_project_lesson_skill(novel_id, title, markdown, task=task, source_path=source_path)


def lesson_suggestions(novel_id: str, limit: int = 20) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    records = list_recent_invocations(nid, limit=limit)
    drafts: list[dict[str, Any]] = []
    for record in records:
        draft = suggest_lesson_from_record(record)
        if draft and not _is_lesson_adopted(draft.get("draft_markdown") or ""):
            drafts.append(draft)
    return {"ok": True, "novel_id": nid, "suggestions": drafts}


def suggest_lesson_from_invocation(novel_id: str, invocation_id: str) -> dict[str, Any] | None:
    record = get_invocation(normalize_novel_id(novel_id), invocation_id)
    if record is None:
        return None
    return suggest_lesson_from_record(record)


def suggest_lesson_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    issues = []
    for item in record.get("harness") or []:
        if not isinstance(item, dict):
            continue
        issues.extend(item.get("issues") or (item.get("result") or {}).get("issues") or [])
    latest_route = _latest(record.get("routes") or [])
    latest_budget = _latest(record.get("budgets") or [])
    if not issues and record.get("status") not in {"failed", "awaiting_confirm"} and latest_budget.get("level") not in {"warn", "error"}:
        return None
    title = _lesson_title(record, issues, latest_route, latest_budget)
    return {
        "id": f"LL-DRAFT-{record.get('id', '')}",
        "title": title,
        "source_invocation_id": record.get("id", ""),
        "task": record.get("task", ""),
        "trigger": {
            "status": record.get("status", ""),
            "harness_issue_codes": [item.get("code", "") for item in issues],
            "route_reason": latest_route.get("reason", ""),
            "budget_level": latest_budget.get("level", "ok"),
        },
        "draft_markdown": _draft_markdown(record, title, issues, latest_route, latest_budget),
    }


def _draft_markdown(
    record: dict[str, Any],
    title: str,
    issues: list[dict[str, Any]],
    route: dict[str, Any],
    budget: dict[str, Any],
) -> str:
    lines = [
        f"# {title}",
        "",
        f"- 来源 invocation：{record.get('id', '')}",
        f"- 任务：{record.get('task', '')}",
        f"- 状态：{record.get('status', '')}",
        "",
        "## 触发条件",
        f"- 路由：{route.get('decision', '')} / {route.get('reason', '')}",
        f"- 预算：{budget.get('level', 'ok')} / {budget.get('estimated_total_tokens', 0)}",
    ]
    lines.extend(f"- Harness：{item.get('code', '')} - {item.get('message', '')}" for item in issues)
    lines.extend([
        "",
        "## 可复用经验",
        "- 待人工确认后写入正式 lessons。",
        "",
        "## 下次执行前检查",
        "- 是否需要新增 SOP predicate 或 prompt assembler 约束。",
        "- 是否应缩小 fanout 信息边界。",
    ])
    return "\n".join(lines).strip() + "\n"


def _lesson_title(record: dict[str, Any], issues: list[dict[str, Any]], route: dict[str, Any], budget: dict[str, Any]) -> str:
    if issues:
        return f"{record.get('task', 'task')} harness failure: {issues[0].get('code', 'issue')}"
    if budget.get("level") in {"warn", "error"}:
        return f"{record.get('task', 'task')} budget guard lesson"
    if route.get("reason"):
        return f"{record.get('task', 'task')} route lesson: {route.get('reason')}"
    return f"{record.get('task', 'task')} invocation lesson"


def _latest(items: list[Any]) -> dict[str, Any]:
    for item in reversed(items):
        if isinstance(item, dict):
            return item
    return {}


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip() or fallback
    return fallback


def _clean_title(title: str) -> str:
    value = re.sub(r"\s+", " ", (title or "").strip())
    return value[:80] or "writing lesson"


def _lesson_digest(markdown: str) -> str:
    return hashlib.sha256((markdown or "").strip().encode("utf-8")).hexdigest()[:8]


def _is_lesson_adopted(markdown: str) -> bool:
    return _existing_lesson_path(_lesson_digest(markdown)) is not None


def _existing_lesson_path(digest: str) -> Path | None:
    if not digest:
        return None
    if not LESSONS_ROOT.exists():
        return None
    for path in LESSONS_ROOT.glob(f"LL-*-{digest}.md"):
        if path.is_file():
            return path
    return None


def _slug(title: str) -> str:
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", title).strip("-").lower()
    return value[:36] or "lesson"


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
