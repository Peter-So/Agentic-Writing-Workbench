from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import DEFAULT_NOVEL_ID, novel_dir, normalize_novel_id
from app.project_paths import project_dir

# 确认→回写链（方案2）：
# 第一层 writeback_on_confirm —— 连续性即时生效（确认即自动，无破坏性）。
# 第二层 archive_chapter —— 成果留档（显式触发，破坏性写章节文件 + 完成状态）。
# NOVEL_DIR 可由环境变量覆盖（测试隔离，避免误写真实 novels/001）。
_NOVEL_DIR = Path(os.getenv("WRITING_NOVEL_DIR") or novel_dir(DEFAULT_NOVEL_ID))


def _novel_dir(novel_id: str | None = None) -> Path:
    if novel_id:
        return novel_dir(novel_id)
    return _NOVEL_DIR


def _chapters_dir(novel_id: str | None = None) -> Path:
    return project_dir(novel_id, "chapters")


def _status_file(novel_id: str | None = None) -> Path:
    try:
        from app.project_structure import resolve_structure_target

        _role, path = resolve_structure_target(novel_id, "chapter_status", create_missing=True)
        if path:
            return path
    except Exception:
        pass
    return project_dir(novel_id, "memory", "章节完成状态.json")


def writeback_on_confirm(task: str, chapter: int | None, accepted: str, track: str = "create",
                         novel_id: str | None = None) -> dict[str, Any]:
    """用户确认/改写后即时回写连续性记忆（不写章节文件）。

    正文：生成结构化章节摘要(含 open_threads 伏笔/resolved 钩子)写入摘要库，
          使下一章 relevant_summaries 立刻能召回 → 远近伏笔不丢。
          并把摘要要点增量入产出向量库（RAG 语义召回，伏笔单独成条）。
    人物/大纲：已由 save_setting 存 Store（调用方处理），此处不重复。
    """
    if task == "prose" and chapter and (accepted or "").strip():
        try:
            from app.chapter_summary import save_chapter_summary, summarize_chapter
            res = summarize_chapter(chapter, accepted)
            if res.get("ok"):
                save_chapter_summary(chapter, res["summary"], novel_id=novel_id)
                _index_quietly(track, "summary", chapter, summary=res["summary"], novel_id=novel_id)
                return {"ok": True, "summarized": True, "chapter": chapter,
                        "open_threads": (res["summary"] or {}).get("open_threads", [])}
            return {"ok": False, "summarized": False, "error": res.get("error", "摘要失败")}
        except Exception as exc:
            return {"ok": False, "summarized": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "summarized": False}


def _index_quietly(track: str, kind: str, chapter: int | None, text: str = "",
                   summary: dict | None = None, novel_id: str | None = None) -> None:
    """增量入产出向量库（容错，失败不阻断确认/留档）。"""
    try:
        from app.output_index import index_confirmed
        index_confirmed(track, kind, chapter, text=text, summary=summary, novel_id=novel_id)
    except Exception:
        pass


def _load_status(novel_id: str | None = None) -> dict[str, Any]:
    try:
        return json.loads(_status_file(novel_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_status(data: dict[str, Any], novel_id: str | None = None) -> None:
    status_file = _status_file(novel_id)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = status_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, status_file)


def _existing_chapter_path(chapter: int, novel_id: str | None = None):
    matches = sorted(_chapters_dir(novel_id).glob(f"chapter-{chapter:02d}-*.md"))
    return matches[0] if matches else None


def resolve_chapter_archive_title(
    chapter: int,
    novel_id: str | None = None,
    request_analysis: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve chapter filename title from backend context, not frontend UI.

    Priority:
    1. Current LLM request analysis if it explicitly names the chapter.
    2. Project Wiki structure map -> outline document -> matched chapter heading.
    3. Empty fallback; archive_chapter will use "未命名" only as a last resort.
    """
    title = _chapter_title_from_analysis(request_analysis or {}, chapter)
    if title:
        return {"title": title, "source": "request_analysis"}
    title = _chapter_title_from_outline(chapter, novel_id)
    if title:
        return {"title": title, "source": "project_wiki_outline"}
    return {"title": "", "source": "fallback"}


def _chapter_title_from_analysis(analysis: dict[str, Any], chapter: int) -> str:
    candidates: list[Any] = [
        analysis.get("chapter_title"),
        analysis.get("target_chapter_title"),
        analysis.get("target_title"),
        analysis.get("title"),
    ]
    for key in ("target_sections", "affected_files"):
        value = analysis.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    for value in candidates:
        text = str(value or "").strip()
        if not text:
            continue
        parsed = _extract_chapter_title(text, chapter)
        if parsed:
            return parsed
    return ""


def _chapter_title_from_outline(chapter: int, novel_id: str | None = None) -> str:
    try:
        from app.project_structure import resolve_structure_target

        _role, outline_path = resolve_structure_target(novel_id, "outline", create_missing=False)
    except Exception:
        outline_path = None
    if not outline_path or not outline_path.exists():
        return ""
    try:
        text = outline_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    cn = _chapter_cn(chapter)
    patterns = [
        rf"^\s*#{{1,6}}\s*第\s*{chapter}\s*章\s*[：:、.\-\s]*(.+?)\s*$",
        rf"^\s*#{{1,6}}\s*第\s*{cn}\s*章\s*[：:、.\-\s]*(.+?)\s*$",
        rf"^\s*#{{1,6}}\s*(?:Ch|Chapter)\s*{chapter}\s*[：:、.\-\s]*(.+?)\s*$",
    ]
    for line in text.splitlines():
        parsed = _extract_chapter_title(line, chapter, patterns=patterns)
        if parsed:
            return parsed
    return ""


def _extract_chapter_title(text: str, chapter: int, patterns: list[str] | None = None) -> str:
    cn = _chapter_cn(chapter)
    checks = patterns or [
        rf"^\s*(?:#{{1,6}}\s*)?第\s*{chapter}\s*章\s*[：:、.\-\s]*(.+?)\s*$",
        rf"^\s*(?:#{{1,6}}\s*)?第\s*{cn}\s*章\s*[：:、.\-\s]*(.+?)\s*$",
        rf"^\s*(?:#{{1,6}}\s*)?(?:Ch|Chapter)\s*{chapter}\s*[：:、.\-\s]*(.+?)\s*$",
    ]
    for pattern in checks:
        match = re.match(pattern, str(text or ""), re.IGNORECASE)
        if match:
            return _strip_title_noise(match.group(1))
    return ""


def _chapter_cn(chapter: int) -> str:
    nums = {
        1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
        6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
    }
    return nums.get(chapter, str(chapter))


def _strip_title_noise(title: str) -> str:
    value = str(title or "").strip()
    value = re.sub(r"[（(]\s*(?:优化版大纲|大纲|正文|草稿|定稿)\s*[）)]", "", value)
    value = re.sub(r"\s+", "", value)
    return value.strip(" -_—：:、，,。.[]【】")


def _safe_chapter_title(
    title: str,
    chapter: int,
    novel_id: str | None = None,
    request_analysis: dict[str, Any] | None = None,
) -> str:
    resolved = resolve_chapter_archive_title(chapter, novel_id, request_analysis)
    value = _strip_title_noise(title) or resolved.get("title") or "未命名"
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.strip(" ._-")
    return (value or "未命名")[:30]


def _placeholder_chapter_path(path: Path) -> bool:
    stem = path.stem
    return stem.endswith("-未命名") or stem.endswith("-untitled")


def _rel(path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)  # 测试把目录重定向到 ROOT 外时退回绝对路径


def archive_chapter(
    chapter: int,
    content: str,
    title: str = "",
    overwrite: bool = False,
    track: str = "create",
    novel_id: str | None = None,
    request_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """显式留档：把确认的正文写入 chapter-NN-*.md，并标记完成状态。

    破坏性写入：已有同章文件时，overwrite=False 则拒绝（防手滑覆盖），返回需确认覆盖。
    """
    if not chapter or not (content or "").strip():
        return {"ok": False, "error": "缺少章节号或内容"}
    nid = normalize_novel_id(novel_id)
    chapters_dir = _chapters_dir(nid)
    chapters_dir.mkdir(parents=True, exist_ok=True)
    title_resolution = resolve_chapter_archive_title(chapter, nid, request_analysis)
    safe_title = _safe_chapter_title(title_resolution.get("title") or title, chapter, nid, request_analysis)
    desired_path = chapters_dir / f"chapter-{chapter:02d}-{safe_title}.md"
    existing = _existing_chapter_path(chapter, nid)
    if existing and not overwrite:
        return {"ok": False, "need_overwrite": True,
                "existing": _rel(existing),
                "target": _rel(desired_path),
                "message": f"第{chapter}章已存在文件，确认覆盖请带 overwrite=true。"}
    if existing:
        if existing != desired_path and _placeholder_chapter_path(existing) and not desired_path.exists():
            path = desired_path
            remove_existing_after_write = existing
        else:
            path = existing
            remove_existing_after_write = None
    else:
        path = desired_path
        remove_existing_after_write = None
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    if remove_existing_after_write and remove_existing_after_write.exists():
        remove_existing_after_write.unlink()
    # 标记章节完成状态
    status = _load_status(nid)
    status[str(chapter)] = {
        "confirmed": True,
        "file": _rel(path),
        "chars": len(content),
        "archived_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_status(status, nid)
    # 留档正文增量入产出向量库（分段语义召回；容错不阻断）。
    _index_quietly(track, "prose", chapter, text=content, novel_id=nid)
    return {
        "ok": True,
        "novel_id": nid,
        "chapter": chapter,
        "file": _rel(path),
        "overwritten": bool(existing),
        "chapter_title": safe_title,
        "title_source": title_resolution.get("source") or ("request_param" if title else "fallback"),
    }


def chapter_status(novel_id: str | None = None) -> dict[str, Any]:
    """返回各章确认/留档状态，供大纲视图与连续性判断。"""
    return _load_status(novel_id)
