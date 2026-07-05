from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.project_kinds import SHORT_FILM_KIND, STRONG_NOVEL_KIND
from app.project_paths import project_dir


STATE_CARD_NAME = "当前状态卡.md"
HOOK_LEDGER_NAME = "伏笔账本.md"
FILM_STATE_NAME = "短片状态卡.md"


def load_creative_state(
    novel_id: str | None,
    *,
    project_kind: str | None = None,
    task: str | None = None,
    max_chars: int = 3000,
) -> dict[str, Any]:
    """Load resumable creative state for generation-time context.

    Missing files are normal for fresh projects and never block generation.
    """
    kind = project_kind or ""
    if kind not in {STRONG_NOVEL_KIND, SHORT_FILM_KIND}:
        return {"ok": True, "available": False, "project_kind": kind, "items": [], "text": ""}
    memory_dir = project_dir(novel_id, "memory", prefer_existing=True)
    candidates = _state_candidates(kind)
    items: list[dict[str, Any]] = []
    for name, label in candidates:
        path = memory_dir / name
        if not path.exists() or not path.is_file():
            continue
        text = _read_excerpt(path, max_chars=max_chars)
        if text:
            items.append({
                "label": label,
                "name": name,
                "path": _safe_rel(path, memory_dir.parent),
                "text": text,
            })
    return {
        "ok": True,
        "available": bool(items),
        "project_kind": kind,
        "task": task or "",
        "items": items,
        "text": format_creative_state(items, project_kind=kind),
        "signals": {
            "creative_state": any(item.get("name") in {STATE_CARD_NAME, FILM_STATE_NAME} for item in items),
            "hook_ledger": any(item.get("name") == HOOK_LEDGER_NAME for item in items),
        },
    }


def update_creative_state_after_archive(
    *,
    novel_id: str | None,
    project_kind: str | None,
    task: str,
    chapter: int | None = None,
    content: str = "",
    request_analysis: dict[str, Any] | None = None,
    archive_file: str = "",
) -> dict[str, Any]:
    """Append a compact archive summary to project memory state files."""
    kind = project_kind or ""
    if kind not in {STRONG_NOVEL_KIND, SHORT_FILM_KIND}:
        return {"ok": True, "updated": False, "reason": "unsupported_project_kind"}
    text = (content or "").strip()
    if not text:
        return {"ok": True, "updated": False, "reason": "empty_content"}
    memory_dir = project_dir(novel_id, "memory", prefer_existing=False)
    memory_dir.mkdir(parents=True, exist_ok=True)
    if kind == SHORT_FILM_KIND:
        return _append_film_state(memory_dir, task=task, content=text, request_analysis=request_analysis or {}, archive_file=archive_file)
    updated = [_append_state_card(memory_dir, task=task, chapter=chapter, content=text, request_analysis=request_analysis or {}, archive_file=archive_file)]
    hook_result = _append_hook_ledger(memory_dir, task=task, chapter=chapter, content=text, request_analysis=request_analysis or {}, archive_file=archive_file)
    if hook_result.get("updated"):
        updated.append(hook_result)
    return {
        "ok": True,
        "updated": any(item.get("updated") for item in updated),
        "files": [item.get("file") for item in updated if item.get("file")],
        "details": updated,
    }


def format_creative_state(items: list[dict[str, Any]], *, project_kind: str = "") -> str:
    if not items:
        return ""
    heading = "## 当前创作状态：状态卡与伏笔账本"
    if project_kind == SHORT_FILM_KIND:
        heading = "## 当前短片状态：状态卡"
    lines = [heading]
    for item in items[:3]:
        lines.append(f"### {item.get('label')}")
        lines.append(str(item.get("text") or "")[:2200])
    lines.append("使用要求：只用于连续性和未完成事项检查，不要把状态说明写进定稿正文。")
    return "\n\n".join(lines)


def _state_candidates(kind: str) -> list[tuple[str, str]]:
    if kind == SHORT_FILM_KIND:
        return [(FILM_STATE_NAME, "短片状态卡")]
    return [(STATE_CARD_NAME, "当前状态卡"), (HOOK_LEDGER_NAME, "伏笔账本")]


def _append_state_card(memory_dir: Path, *, task: str, chapter: int | None, content: str, request_analysis: dict[str, Any], archive_file: str) -> dict[str, Any]:
    path = memory_dir / STATE_CARD_NAME
    _ensure_header(path, "# 当前状态卡\n\n")
    summary = _summarize_content(content)
    stage = request_analysis.get("creative_stage_label") or request_analysis.get("creative_stage") or task
    block = [
        f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}｜{stage}",
        f"- 任务：{task}",
        f"- 章节：{chapter or '未指定'}",
        f"- 归档：{archive_file or '未记录'}",
        f"- 当前状态：{summary}",
    ]
    _append_text(path, "\n".join(block) + "\n")
    return {"updated": True, "file": str(path)}


def _append_hook_ledger(memory_dir: Path, *, task: str, chapter: int | None, content: str, request_analysis: dict[str, Any], archive_file: str) -> dict[str, Any]:
    if task not in {"outline", "beat_sheet", "plot", "prose", "expansion", "fix"}:
        return {"updated": False, "reason": "task_without_hooks"}
    hooks = _extract_hook_lines(content)
    if not hooks and task not in {"prose", "outline", "beat_sheet"}:
        return {"updated": False, "reason": "no_hook_signal"}
    path = memory_dir / HOOK_LEDGER_NAME
    _ensure_header(path, "# 伏笔账本\n\n")
    block = [
        f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}｜{task}｜第{chapter or '?'}章",
        f"- 归档：{archive_file or '未记录'}",
    ]
    if hooks:
        block.extend(f"- 线索：{line}" for line in hooks[:8])
    else:
        block.append("- 线索：本轮未显式识别到伏笔/钩子词，后续续写需人工确认是否新增未回收事项。")
    _append_text(path, "\n".join(block) + "\n")
    return {"updated": True, "file": str(path)}


def _append_film_state(memory_dir: Path, *, task: str, content: str, request_analysis: dict[str, Any], archive_file: str) -> dict[str, Any]:
    path = memory_dir / FILM_STATE_NAME
    _ensure_header(path, "# 短片状态卡\n\n")
    summary = _summarize_content(content)
    block = [
        f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}｜{task}",
        f"- 归档：{archive_file or '未记录'}",
        f"- 当前状态：{summary}",
    ]
    _append_text(path, "\n".join(block) + "\n")
    return {"ok": True, "updated": True, "files": [str(path)]}


def _read_excerpt(path: Path, *, max_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _ensure_header(path: Path, header: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header, encoding="utf-8")


def _append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def _summarize_content(content: str, *, max_chars: int = 360) -> str:
    clean = re.sub(r"\s+", " ", content or "").strip()
    return clean[:max_chars] + ("..." if len(clean) > max_chars else "")


def _extract_hook_lines(content: str) -> list[str]:
    hooks: list[str] = []
    for line in re.split(r"[\r\n]+", content or ""):
        clean = line.strip(" -\t")
        if not clean:
            continue
        if any(word in clean for word in ["伏笔", "钩子", "悬念", "线索", "未解", "回收", "秘密", "异常"]):
            hooks.append(clean[:220])
    return hooks


def _safe_rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(path)
