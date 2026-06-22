from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import normalize_novel_id
from app.project_paths import outputs_dir


CN_NUMS = {
    1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
    6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
}


def archive_outline_chapter(
    *,
    novel_id: str | None,
    chapter: int,
    content: str,
    overwrite: bool = False,
    track: str = "create",
) -> dict[str, Any]:
    """Replace one chapter section in the project's outline after human confirmation."""
    if not chapter or chapter < 1:
        return {"ok": False, "error": "缺少有效章节号"}
    accepted = (content or "").strip()
    if not accepted:
        return {"ok": False, "error": "缺少要写回的大纲内容"}

    nid = normalize_novel_id(novel_id)
    outline = _outline_file(nid)
    if not outline:
        return {"ok": False, "error": "未找到大纲文件"}

    text = outline.read_text(encoding="utf-8", errors="replace")
    section = _find_chapter_section(text, chapter)
    if not section:
        return {"ok": False, "error": f"未在 {outline.name} 找到第{chapter}章标题"}
    start, end, heading = section

    if not overwrite:
        return {
            "ok": False,
            "need_overwrite": True,
            "existing": _rel(outline),
            "message": f"将替换 {outline.name} 中第{chapter}章大纲；确认后请带 overwrite=true。",
        }

    cleaned = _clean_outline_content(accepted, chapter)
    if not cleaned.strip():
        return {"ok": False, "error": "未能从采纳内容中提取有效大纲正文"}
    replacement = _normalize_replacement(cleaned, heading, chapter)
    backup = _backup_outline(outline, nid)
    tmp = outline.with_suffix(outline.suffix + ".tmp")
    tmp.write_text(text[:start] + replacement.rstrip() + "\n\n" + text[end:].lstrip("\n"), encoding="utf-8")
    os.replace(tmp, outline)
    _index_quietly(track, chapter, replacement, nid)
    return {
        "ok": True,
        "novel_id": nid,
        "chapter": chapter,
        "file": _rel(outline),
        "backup": _rel(backup),
        "overwritten": True,
    }


def _outline_file(novel_id: str) -> Path | None:
    try:
        from app.project_structure import find_related_structure_file, resolve_structure_target

        _role, routed = resolve_structure_target(novel_id, "outline", create_missing=True)
        if routed and routed.is_file():
            return routed
        matched = find_related_structure_file(novel_id, "outline")
        if matched and matched[1].is_file():
            return matched[1]
    except Exception:
        pass
    return None


def _find_chapter_section(text: str, chapter: int) -> tuple[int, int, str] | None:
    cn = CN_NUMS.get(chapter, str(chapter))
    patterns = [
        rf"^#{{1,6}}\s*第\s*{chapter}\s*章\b.*$",
        rf"^#{{1,6}}\s*第\s*{cn}\s*章\b.*$",
        rf"^#{{1,6}}\s*Chapter\s*{chapter}\b.*$",
        rf"^#{{1,6}}\s*Ch\s*{chapter}\b.*$",
    ]
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line)
    start_idx = -1
    level = 0
    for idx, line in enumerate(lines):
        raw = line.rstrip("\r\n")
        if any(re.match(pattern, raw, re.IGNORECASE) for pattern in patterns):
            start_idx = idx
            level = len(raw) - len(raw.lstrip("#"))
            break
    if start_idx < 0:
        return None
    end_idx = len(lines)
    for idx in range(start_idx + 1, len(lines)):
        raw = lines[idx].rstrip("\r\n")
        if re.match(r"^#{1,6}\s+", raw):
            next_level = len(raw) - len(raw.lstrip("#"))
            if next_level <= level:
                end_idx = idx
                break
    start = offsets[start_idx]
    end = offsets[end_idx] if end_idx < len(offsets) else len(text)
    return start, end, lines[start_idx].rstrip("\r\n")


def _normalize_replacement(content: str, heading: str, chapter: int) -> str:
    cn = CN_NUMS.get(chapter, str(chapter))
    lines = content.splitlines()
    first = lines[0].strip() if lines else ""
    has_heading = bool(re.match(rf"^#{{1,6}}\s*第\s*({chapter}|{cn})\s*章\b", first))
    if has_heading:
        return content
    if re.match(rf"^第\s*({chapter}|{cn})\s*章\b", first):
        lines[0] = heading
        return "\n".join(lines).strip()
    return f"{heading}\n\n{content}"


def _clean_outline_content(content: str, chapter: int) -> str:
    """Extract only the target chapter outline body from an accepted model answer."""
    text = (content or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    text = _strip_code_fence(text)
    chapter_block = _extract_chapter_block_from_answer(text, chapter)
    if chapter_block:
        text = chapter_block
    lines = text.splitlines()
    cleaned: list[str] = []
    skip_section = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        if _is_wrapper_line(stripped, chapter):
            continue
        if _is_meta_section_heading(stripped):
            skip_section = True
            continue
        if skip_section:
            if re.match(r"^#{1,6}\s+", stripped):
                skip_section = False
            else:
                continue
        cleaned.append(line.rstrip())
    return "\n".join(cleaned).strip()


def clean_outline_archive_content(content: str, chapter: int) -> str:
    """Public adapter for extracting archive-safe outline content."""
    return _clean_outline_content(content, chapter)


def _strip_code_fence(text: str) -> str:
    match = re.match(r"^```(?:markdown|md|text)?\s*\n([\s\S]*?)\n```\s*$", text.strip(), re.IGNORECASE)
    return match.group(1).strip() if match else text


def _extract_chapter_block_from_answer(text: str, chapter: int) -> str:
    found = _find_chapter_section(text, chapter)
    if not found:
        return ""
    start, end, _heading = found
    return text[start:end].strip()


def _is_wrapper_line(line: str, chapter: int) -> bool:
    cn = CN_NUMS.get(chapter, str(chapter))
    plain = re.sub(r"^\*\*(.*?)\*\*$", r"\1", line).strip()
    version_words = r"(优化版大纲|优化版本大纲|大纲优化版|大纲优化版本|优化后大纲|新版大纲|修订版大纲)"
    wrappers = [
        rf"^#{{1,6}}\s*{version_words}\s*[：:]?$",
        rf"^#{{1,6}}\s*(第\s*({chapter}|{cn})\s*章)?\s*{version_words}\s*[：:]?$",
        r"^#{1,6}\s*(第\s*(" + str(chapter) + "|" + cn + r")\s*章).*(优化版|优化版本|优化后|新版|修订版).*(大纲)?\s*$",
        rf"^[（(]?({version_words}|以下为优化版|以下为优化版本)[）)]?[：:]?$",
        r"^\*\*(" + version_words + r"|第\s*(" + str(chapter) + "|" + cn + r")\s*章.*优化.*)\*\*[：:]?$",
        r"^(以下是|下面是).*(优化|调整).*(大纲|第三章|第" + cn + r"章).*$",
    ]
    return any(re.match(pattern, candidate) for candidate in {line, plain} for pattern in wrappers)


def _is_meta_section_heading(line: str) -> bool:
    cleaned = re.sub(r"^[\-\*\d\.\s]+", "", line).strip()
    cleaned = re.sub(r"^\*\*(.*?)\*\*$", r"\1", cleaned).strip()
    keywords = r"(修改建议|优化建议|调整建议|优化要点|修改要点|本轮优化|本轮调整|本轮优化要点|本轮调整要点|优化说明|修改说明|调整说明|理由|思路|变化|备注|总结)"
    freeform_meta = r"(修改建议|优化建议|调整建议|优化要点|修改要点|本轮优化|本轮调整|本轮优化要点|本轮调整要点|优化说明|修改说明|调整说明)"
    return bool(re.match(rf"^#{{1,6}}\s*{keywords}\s*[：:]?$", cleaned)
                or re.match(rf"^[（(]?{keywords}[）)]?\s*[：:].*$", cleaned)
                or re.match(rf"^[（(]?{freeform_meta}[）)]?\S+.*$", cleaned)
                or re.match(rf"^[（(]?{keywords}[）)]?\s*$", cleaned))


def _backup_outline(outline: Path, novel_id: str) -> Path:
    out_dir = outputs_dir(novel_id, "大纲备份")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = out_dir / f"{outline.stem}_{stamp}{outline.suffix}"
    backup.write_text(outline.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return backup


def _index_quietly(track: str, chapter: int, content: str, novel_id: str) -> None:
    try:
        from app.output_index import index_confirmed

        index_confirmed(track, "outline", chapter, text=content, novel_id=novel_id)
    except Exception:
        pass


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
