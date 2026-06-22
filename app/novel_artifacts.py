from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import novel_dir, normalize_novel_id
from app.project_paths import outputs_dir


NOVEL_TARGETS = {
    "logline": "base_setting",
    "brief": "base_setting",
    "materials": "base_setting",
    "generic": "base_setting",
    "setting": "base_setting",
    "world": "worldview",
    "worldview": "worldview",
    "character": "character",
    "characters": "character",
    "beat_sheet": "plot",
    "plot": "plot",
    "outline": "outline",
    "expansion": "输出/expansion_latest.md",
    "fix": "输出/fix_latest.md",
}

TARGET_TASKS = {
    "基础设定.md": "setting",
    "人物档案.md": "character",
    "世界观设定.md": "world",
    "情节设定.md": "beat_sheet",
    "大纲.md": "outline",
}


def task_for_target(target: str) -> str | None:
    return TARGET_TASKS.get(Path(str(target or "")).name)


def ensure_novel_files(novel_id: str | None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    from app.project_structure import ensure_project_structure_wiki

    return ensure_project_structure_wiki(nid, project_kind="novel_strong", create_missing=True)


def save_novel_artifact(
    *,
    task: str,
    content: str,
    novel_id: str | None,
    chapter: int | None = None,
    track: str = "create",
) -> dict[str, Any]:
    """Persist confirmed planning output for strong novel projects.

    This is intentionally append-only for planning files. Replacing an existing
    chapter outline or chapter prose still goes through the explicit archive
    endpoints with overwrite confirmation.
    """
    text = clean_planning_content(task, content, chapter)
    if not text:
        return {"ok": False, "error": "缺少可保存的确认内容"}
    nid = normalize_novel_id(novel_id)
    ensure_novel_files(nid)
    base = novel_dir(nid)
    target_key = NOVEL_TARGETS.get(task, f"输出/{task}_latest.md")
    if "/" in target_key or target_key.endswith(".md"):
        target = (base / target_key).resolve()
    else:
        from app.project_structure import resolve_structure_target

        _role, routed = resolve_structure_target(nid, target_key, create_missing=True)
        target = (routed or (outputs_dir(nid) / f"{task}_latest.md")).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return {"ok": False, "error": "目标文件越界"}

    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
    block = _confirmed_block(task, text, chapter)
    if block not in old:
        _atomic_write(target, old.rstrip() + "\n\n" + block + "\n")
    snapshot = _snapshot(base, task, text, chapter)
    _index_quietly(track, task, chapter, text, nid)
    return {
        "ok": True,
        "novel_id": nid,
        "task": task,
        "chapter": chapter,
        "file": _rel(target),
        "snapshot": _rel(snapshot),
        "mode": "append",
    }


def clean_planning_content(task: str, content: str, chapter: int | None = None) -> str:
    text = _strip_code_fence((content or "").replace("\r\n", "\n").strip())
    if not text:
        return ""
    if task == "outline":
        try:
            from app.outline_writeback import _clean_outline_content

            return _clean_outline_content(text, chapter or 1).strip()
        except Exception:
            pass
    return _drop_meta_sections(text)


def _drop_meta_sections(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if _is_wrapper_line(stripped):
            continue
        if _is_meta_heading(stripped):
            skipping = True
            continue
        if skipping:
            if re.match(r"^#{1,6}\s+", stripped):
                skipping = False
            else:
                continue
        out.append(line.rstrip())
    return "\n".join(out).strip()


def _strip_code_fence(text: str) -> str:
    match = re.match(r"^```(?:markdown|md|text)?\s*\n([\s\S]*?)\n```\s*$", text.strip(), re.IGNORECASE)
    return match.group(1).strip() if match else text


def _is_wrapper_line(line: str) -> bool:
    return bool(re.match(r"^[（(]?(以下为|下面是|优化版|正式版|最终版).{0,24}[：:]?$", line))


def _is_meta_heading(line: str) -> bool:
    return bool(re.match(
        r"^#{1,6}\s*(本轮)?(优化|修改|调整|改动|处理)?(要点|说明|建议|理由|思路|变化|备注|总结)\s*$",
        line,
    ) or re.match(
        r"^[（(]?(本轮)?(优化|修改|调整|改动|处理)?(要点|说明|建议|理由|思路|变化|备注|总结)[）)]?[：:]?$",
        line,
    ))


def _confirmed_block(task: str, text: str, chapter: int | None) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    chapter_part = f"｜第{chapter}章" if chapter else ""
    return (
        f"<!--CONFIRMED:{task}:{datetime.now().strftime('%Y%m%d%H%M%S')}-->\n"
        f"## 用户确认稿｜{_task_label(task)}{chapter_part}｜{stamp}\n\n"
        f"{text.strip()}\n"
        f"<!--/CONFIRMED:{task}-->"
    )


def _snapshot(base: Path, task: str, text: str, chapter: int | None) -> Path:
    out_dir = outputs_dir(base.name, "已采纳规划")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ch = f"_ch{chapter:02d}" if chapter else ""
    path = out_dir / f"{task}{ch}_{stamp}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _write_missing(base: Path, files: dict[str, str]) -> list[Path]:
    created: list[Path] = []
    for name, content in files.items():
        target = base / name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created.append(target)
    return created


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _index_quietly(track: str, task: str, chapter: int | None, text: str, novel_id: str) -> None:
    try:
        from app.output_index import index_confirmed

        index_confirmed(track, task, chapter, text=text, novel_id=novel_id)
    except Exception:
        pass


def _task_label(task: str) -> str:
    return {
        "logline": "基础设定",
        "brief": "基础设定",
        "materials": "基础设定",
        "setting": "基础设定",
        "world": "世界观",
        "worldview": "世界观",
        "character": "人物设定",
        "characters": "人物设定",
        "beat_sheet": "情节设定",
        "plot": "情节设定",
        "outline": "大纲",
        "expansion": "扩写",
        "fix": "修订",
    }.get(task, task)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
