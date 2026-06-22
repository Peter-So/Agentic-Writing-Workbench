from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import novel_dir, normalize_novel_id
from app.project_paths import outputs_dir
from app.project_kinds import DEFAULT_KIND, SHORT_FILM_KIND, project_kind


SHORT_FILM_TARGETS = {
    "brief": "brief",
    "logline": "concept",
    "concept": "concept",
    "character": "character",
    "outline": "beat_sheet",
    "beat_sheet": "beat_sheet",
    "screenplay": "screenplay",
    "prose": "screenplay",
    "shot_list": "shot_list",
    "style": "style",
    "fix": "输出/fix_latest.md",
}

GENERIC_TARGETS = {
    "outline": "ideas",
    "materials": "inbox",
    "draft": "draft",
    "prose": "draft",
    "fix": "输出/fix_latest.md",
    "references": "references",
    "reference": "references",
}


def target_for_task(novel_id: str | None, task: str) -> tuple[str, str]:
    kind = project_kind(novel_id)
    if kind == SHORT_FILM_KIND:
        return kind, SHORT_FILM_TARGETS.get(task, f"输出/{task}_latest.md")
    if kind == DEFAULT_KIND:
        return kind, GENERIC_TARGETS.get(task, f"输出/{task}_latest.md")
    return kind, f"输出/{task}_latest.md"


def save_artifact(
    task: str,
    content: str,
    novel_id: str | None = None,
    overwrite: bool = False,
    track: str = "create",
) -> dict[str, Any]:
    """Save confirmed non-chapter project output into the selected project.

    Strong novel chapters still use archive_chapter. This helper is for project
    artifacts such as short-film logline/screenplay/shot list and generic drafts.
    """
    if not (content or "").strip():
        return {"ok": False, "error": "缺少要保存的内容"}
    nid = normalize_novel_id(novel_id)
    kind, rel_target = target_for_task(nid, task)
    base = novel_dir(nid)
    if "/" in rel_target or rel_target.endswith(".md"):
        target = (base / rel_target).resolve()
    else:
        try:
            from app.project_structure import resolve_structure_target
            _role, routed = resolve_structure_target(nid, rel_target, create_missing=True)
            target = (routed or (outputs_dir(nid) / f"{task}_latest.md")).resolve()
        except Exception:
            target = (outputs_dir(nid) / f"{task}_latest.md").resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return {"ok": False, "error": "目标文件越界"}
    exists = target.exists() and target.read_text(encoding="utf-8", errors="replace").strip()
    if exists and not overwrite:
        return {
            "ok": False,
            "need_overwrite": True,
            "existing": _rel(target),
            "message": f"{target.name} 已有内容，确认覆盖请带 overwrite=true。",
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)

    snap = _snapshot(base, task, content)
    _index_quietly(track, task, content, nid)
    return {
        "ok": True,
        "novel_id": nid,
        "project_kind": kind,
        "task": task,
        "file": _rel(target),
        "snapshot": _rel(snap),
        "overwritten": bool(exists),
    }


def _snapshot(base: Path, task: str, content: str) -> Path:
    out_dir = outputs_dir(base.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{task}_accepted_{stamp}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _index_quietly(track: str, task: str, content: str, novel_id: str) -> None:
    try:
        from app.output_index import index_confirmed
        index_confirmed(track, task, None, text=content, novel_id=novel_id)
    except Exception:
        pass


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
