from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import novel_dir, normalize_novel_id


CN_NUMS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def locate_prose_targets(
    *,
    novel_id: str | None,
    chapter: int | None,
    analysis: dict[str, Any] | None = None,
    message: str | None = "",
    max_locations: int = 5,
) -> list[dict[str, Any]]:
    """Locate chapter prose passages with file paths and line ranges.

    This is the single reusable locator for prose refinement/rewrite tasks. It
    deliberately stays deterministic: LLM intent supplies anchors, while this
    module resolves those anchors against project files and returns recoverable
    file/line facts for pending intent, material assembly, and UI confirmation.
    """
    ch = _clean_chapter(chapter, analysis, message)
    if not ch:
        return []
    path = _chapter_file(novel_id, ch)
    if not path or not path.exists():
        return []
    lines = _read_lines(path)
    if not lines:
        return []
    anchors = _anchors(analysis or {}, message or "")
    locations: list[dict[str, Any]] = []
    for anchor in anchors:
        loc = _match_anchor(path, lines, ch, anchor)
        if loc:
            locations.append(loc)
    if not locations:
        locations.append(_chapter_location(path, lines, ch))
    unique: dict[str, dict[str, Any]] = {}
    for loc in sorted(locations, key=lambda item: item.get("score", 0), reverse=True):
        key = f"{loc.get('path')}:{loc.get('start_line')}:{loc.get('end_line')}"
        unique.setdefault(key, loc)
        if len(unique) >= max_locations:
            break
    return list(unique.values())


def is_prose_refinement_intent(analysis: dict[str, Any] | None, task: str | None = None) -> bool:
    data = analysis or {}
    task_key = str(data.get("task") or task or "").strip().lower()
    stage = str(data.get("creative_stage") or "").strip().lower()
    role = str(data.get("target_role") or "").strip().lower()
    materials = {str(item).strip().lower() for item in data.get("affected_materials") or []}
    files = " ".join(str(item) for item in data.get("affected_files") or [])
    has_anchor = bool(data.get("target_sections") or data.get("prose_locations"))
    intent = str(data.get("intent") or "").strip().lower()
    deliverable = str(data.get("deliverable") or "").strip().lower()
    prose_task_is_refinement = task_key in {"fix", "expansion"} or (
        task_key == "prose" and (has_anchor or intent == "revise" or deliverable == "rewrite")
    )
    return (
        prose_task_is_refinement
        and (
            stage == "prose"
            or role in {"chapter_body", "prose"}
            or "chapter_body" in materials
            or "章节正文" in files
            or "正文" in files
        )
    )


def _chapter_file(novel_id: str | None, chapter: int) -> Path | None:
    base = novel_dir(normalize_novel_id(novel_id)) / "正文"
    candidates = sorted(base.glob(f"chapter-{chapter:02d}*.md"))
    if candidates:
        return candidates[0]
    candidates = sorted(base.glob(f"*{chapter:02d}*.md"))
    if candidates:
        return candidates[0]
    return None


def _match_anchor(path: Path, lines: list[str], chapter: int, anchor: str) -> dict[str, Any] | None:
    normalized_anchor = _normalize(anchor)
    if len(normalized_anchor) < 2:
        return None
    window_count = 3 if len(anchor) > 30 else 1
    best: tuple[float, int, int, str] | None = None
    for idx in range(len(lines)):
        end_idx = min(len(lines), idx + window_count)
        window = "\n".join(lines[idx:end_idx])
        normalized_window = _normalize(window)
        if not normalized_window:
            continue
        if normalized_anchor in normalized_window:
            score = 1.0
            match_kind = "exact"
        else:
            score = SequenceMatcher(None, normalized_anchor, normalized_window).ratio()
            overlap = _token_overlap(normalized_anchor, normalized_window)
            score = max(score, overlap)
            match_kind = "fuzzy"
        if score < 0.42:
            continue
        if not best or score > best[0]:
            best = (score, idx, end_idx, match_kind)
    if not best:
        return None
    score, start_idx, end_idx, match_kind = best
    start = max(0, start_idx - 1)
    end = min(len(lines), end_idx + 2)
    return _location(
        path=path,
        chapter=chapter,
        start_line=start + 1,
        end_line=end,
        match=match_kind,
        score=round(float(score), 4),
        anchor=anchor,
        excerpt="\n".join(lines[start:end]).strip(),
    )


def _chapter_location(path: Path, lines: list[str], chapter: int) -> dict[str, Any]:
    start, end = _chapter_heading_span(lines, chapter)
    if not start:
        start, end = 1, min(len(lines), 30)
    return _location(
        path=path,
        chapter=chapter,
        start_line=start,
        end_line=end,
        match="chapter_file",
        score=0.2,
        anchor=f"第{chapter}章",
        excerpt="\n".join(lines[start - 1:min(end, start + 14)]).strip(),
    )


def _chapter_heading_span(lines: list[str], chapter: int) -> tuple[int | None, int | None]:
    cn = next((k for k, v in CN_NUMS.items() if v == chapter and k != "两"), str(chapter))
    patterns = [
        rf"^#{{1,6}}\s*第\s*{chapter}\s*章\b",
        rf"^#{{1,6}}\s*第\s*{cn}\s*章\b",
        rf"^#{{1,6}}\s*(Ch|Chapter)\s*{chapter}\b",
    ]
    for idx, line in enumerate(lines):
        if not any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
            continue
        level = len(line) - len(line.lstrip("#"))
        end = len(lines)
        for j in range(idx + 1, len(lines)):
            if re.match(r"^#{1,6}\s+", lines[j]):
                next_level = len(lines[j]) - len(lines[j].lstrip("#"))
                if next_level <= level:
                    end = j
                    break
        return idx + 1, end
    return None, None


def _anchors(analysis: dict[str, Any], message: str) -> list[str]:
    raw: list[str] = []
    for key in ("target_sections", "plot_points"):
        value = analysis.get(key)
        if isinstance(value, list):
            raw.extend(str(item) for item in value)
    raw.extend(_quoted_fragments(message))
    raw.extend(_line_hints(message))
    seen: set[str] = set()
    anchors: list[str] = []
    for item in raw:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if len(text) < 2 or text in seen:
            continue
        seen.add(text)
        anchors.append(text[:160])
        if len(anchors) >= 10:
            break
    return anchors


def _quoted_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    for pattern in [r"“([^”]{2,160})”", r"\"([^\"]{2,160})\"", r"‘([^’]{2,160})’"]:
        fragments.extend(match.group(1).strip() for match in re.finditer(pattern, text or ""))
    return fragments


def _line_hints(text: str) -> list[str]:
    hints: list[str] = []
    for match in re.finditer(r"(第\s*\d+\s*(?:行|段)|\d+\s*-\s*\d+\s*行)", text or ""):
        hints.append(match.group(1))
    return hints


def _clean_chapter(chapter: int | None, analysis: dict[str, Any] | None, message: str | None) -> int | None:
    for value in [chapter, (analysis or {}).get("target_chapter")]:
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            parsed = int(value.strip())
            if parsed > 0:
                return parsed
    text = message or ""
    match = re.search(r"第\s*(\d+)\s*章", text)
    if match:
        return int(match.group(1))
    match = re.search(r"第\s*([一二两三四五六七八九十])\s*章", text)
    if match:
        return CN_NUMS.get(match.group(1))
    return None


def _location(
    *,
    path: Path,
    chapter: int,
    start_line: int,
    end_line: int,
    match: str,
    score: float,
    anchor: str,
    excerpt: str,
) -> dict[str, Any]:
    return {
        "role": "chapter_body",
        "target": "章节正文",
        "path": _rel(path),
        "exists": path.exists(),
        "chapter": chapter,
        "start_line": start_line,
        "end_line": end_line,
        "matched": anchor,
        "match": match,
        "score": score,
        "anchor": anchor,
        "excerpt": excerpt[:1000],
    }


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())


def _token_overlap(anchor: str, window: str) -> float:
    if not anchor or not window:
        return 0.0
    chars = set(anchor)
    if not chars:
        return 0.0
    return len(chars.intersection(set(window))) / len(chars)
