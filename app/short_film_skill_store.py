from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import WRITING_ROOT, novel_dir, normalize_novel_id
from app.project_paths import skills_dir
from app.project_kinds import SHORT_FILM_KIND, project_kind


SHORT_FILM_SKILL_SUITE = WRITING_ROOT / "short-film-skill-suite"
SHARED_SHORT_FILM_SKILL = SHORT_FILM_SKILL_SUITE / "shared-lessons-skill.md"

PUBLIC_SKILL_FILES = [
    "screenplay-skill.md",
    "storyboard-skill.md",
    "character-visual-skill.md",
    "image-consistency-style-guide.md",
]

TASK_TO_SKILLS = {
    "logline": ["screenplay-skill.md", "lessons-skill.md"],
    "character": ["screenplay-skill.md", "character-visual-skill.md", "lessons-skill.md"],
    "beat_sheet": ["screenplay-skill.md", "storyboard-skill.md", "lessons-skill.md"],
    "screenplay": ["screenplay-skill.md", "storyboard-skill.md", "lessons-skill.md"],
    "prose": ["screenplay-skill.md", "storyboard-skill.md", "lessons-skill.md"],
    "shot_list": ["storyboard-skill.md", "image-consistency-style-guide.md", "lessons-skill.md"],
    "fix": ["screenplay-skill.md", "storyboard-skill.md", "character-visual-skill.md", "lessons-skill.md"],
}


def public_short_film_skill_files() -> list[Path]:
    if not SHORT_FILM_SKILL_SUITE.is_dir():
        return []
    files = [SHORT_FILM_SKILL_SUITE / name for name in PUBLIC_SKILL_FILES if (SHORT_FILM_SKILL_SUITE / name).is_file()]
    if SHARED_SHORT_FILM_SKILL.is_file():
        files.append(SHARED_SHORT_FILM_SKILL)
    return files


def project_short_film_skill_files(novel_id: str | None) -> list[Path]:
    root = skills_dir(novel_id)
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*.md") if path.is_file())


def skill_counts(novel_id: str | None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    public_files = public_short_film_skill_files() if project_kind(nid) == SHORT_FILM_KIND else []
    project_files = project_short_film_skill_files(nid)
    return {
        "count": len(public_files) + len(project_files),
        "public_count": len(public_files),
        "project_count": len(project_files),
        "public_root": _rel(SHORT_FILM_SKILL_SUITE) if SHORT_FILM_SKILL_SUITE.exists() else "",
        "project_root": _rel(skills_dir(nid)),
        "files": [
            {"name": path.name, "path": _rel(path), "source": "public_short_film"}
            for path in public_files
        ] + [
            {"name": path.name, "path": _rel(path), "source": "project"}
            for path in project_files
        ],
    }


def registry_items(novel_id: str | None, task: str = "") -> list[dict[str, Any]]:
    nid = normalize_novel_id(novel_id)
    items: list[dict[str, Any]] = []
    if project_kind(nid) == SHORT_FILM_KIND:
        for path in public_short_film_skill_files():
            text = _read(path)
            items.append(_item(path, path.name, text, "public_short_film", _relevant(path.name, task)))
    for path in project_short_film_skill_files(nid):
        text = _read(path)
        items.append(_item(path, path.name, text, "project", _relevant(path.name, task)))
    return items


def load_short_film_skill_text(novel_id: str | None, task: str, max_chars: int = 12000) -> str:
    parts: list[str] = []
    remaining = max_chars
    for item in registry_items(novel_id, task):
        if not item.get("relevant"):
            continue
        path = ROOT / item["path"]
        text = _read(path)
        if not text:
            continue
        take = min(8000, remaining)
        parts.append(f"## {item['path']}（{item['source_label']}）\n{text[:take]}")
        remaining -= take
        if remaining <= 500:
            break
    return "\n\n".join(parts)


def load_style_guide(novel_id: str | None) -> str:
    nid = normalize_novel_id(novel_id)
    project_path = skills_dir(nid) / "image-consistency-style-guide.md"
    if project_path.is_file():
        return _read(project_path)
    return _read(SHORT_FILM_SKILL_SUITE / "image-consistency-style-guide.md")


def classify_skill_scope(novel_id: str | None, title: str, markdown: str, task: str = "") -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    text = f"{title}\n{markdown}".strip()
    if project_kind(nid) != SHORT_FILM_KIND:
        return {"scope": "project", "reason": "非电影脚本项目，保留为项目私有技能。"}
    project_hits = _project_specific_hits(nid, text)
    general_hits = [marker for marker in [
        "短片", "电影", "剧本", "分镜", "镜头", "角色", "对白", "场景", "节拍",
        "可拍摄", "画面", "声音", "生图", "四视图", "一致性", "规则", "规范", "必须", "不要", "禁止",
    ] if marker in text]
    if project_hits:
        return {
            "scope": "project",
            "reason": "包含项目专有线索，避免污染电影脚本公共技能库。",
            "project_hits": project_hits[:8],
            "general_hits": general_hits[:8],
        }
    if len(general_hits) >= 2 or task in TASK_TO_SKILLS:
        return {
            "scope": "public_short_film",
            "reason": "未发现项目专有线索，且内容像可复用电影脚本技法。",
            "project_hits": [],
            "general_hits": general_hits[:8],
        }
    return {
        "scope": "project",
        "reason": "通用性证据不足，按保守策略保留在项目私有技能。",
        "project_hits": [],
        "general_hits": general_hits[:8],
    }


def append_lesson_to_skill_store(
    novel_id: str | None,
    title: str,
    markdown: str,
    *,
    task: str = "",
    source_path: str = "",
) -> dict[str, Any]:
    decision = classify_skill_scope(novel_id, title, markdown, task=task)
    if decision["scope"] == "public_short_film":
        result = append_public_lesson_skill(title, markdown, task=task, source_path=source_path)
    else:
        result = append_project_lesson_skill(novel_id, title, markdown, task=task, source_path=source_path)
    result["scope"] = decision["scope"]
    result["scope_reason"] = decision["reason"]
    if decision.get("project_hits"):
        result["project_hits"] = decision["project_hits"]
    if decision.get("general_hits"):
        result["general_hits"] = decision["general_hits"]
    return result


def append_public_lesson_skill(title: str, markdown: str, task: str = "", source_path: str = "") -> dict[str, Any]:
    SHORT_FILM_SKILL_SUITE.mkdir(parents=True, exist_ok=True)
    header = "# 电影脚本公共经验技能\n\n人工确认后沉淀的通用电影短片创作经验，供所有电影脚本项目复用。\n"
    return _append_lesson_block(
        SHARED_SHORT_FILM_SKILL,
        header,
        title,
        markdown,
        task=task,
        source_path=source_path,
        source_label="public_short_film_shared",
    )


def append_project_lesson_skill(novel_id: str | None, title: str, markdown: str, task: str = "", source_path: str = "") -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    root = skills_dir(nid)
    root.mkdir(parents=True, exist_ok=True)
    return _append_lesson_block(
        root / "lessons_skill.md",
        "# 项目经验技能卡\n",
        title,
        markdown,
        task=task,
        source_path=source_path,
        source_label="project",
    )


def _relevant(name: str, task: str) -> bool:
    if not task:
        return True
    return name in TASK_TO_SKILLS.get(task, []) or name == "lessons_skill.md"


def _item(path: Path, name: str, text: str, source: str, relevant: bool) -> dict[str, Any]:
    return {
        "name": name,
        "path": _rel(path),
        "title": _title(text, path.stem),
        "chars": len(text),
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "source": source,
        "source_label": "电影脚本公共技能库" if source == "public_short_film" else "项目沉淀技能",
        "relevant": relevant,
    }


def _append_lesson_block(
    path: Path,
    header: str,
    title: str,
    markdown: str,
    *,
    task: str = "",
    source_path: str = "",
    source_label: str = "",
) -> dict[str, Any]:
    existing = _read(path) if path.exists() else header
    marker = f"<!--LESSON:{hashlib.sha256(markdown.encode('utf-8')).hexdigest()[:12]}-->"
    if marker in existing:
        return {"ok": True, "created": False, "path": _rel(path), "task": task or "all", "store": source_label}
    block = [
        "",
        marker,
        f"## {title}",
        "",
        f"- 适用任务：{task or '通用'}",
        f"- 来源：{source_path or 'manual'}",
        f"- 归属：{source_label}",
        "",
        _compact_lesson(markdown),
        "",
    ]
    path.write_text(existing.rstrip() + "\n\n" + "\n".join(block).strip() + "\n", encoding="utf-8", newline="")
    return {"ok": True, "created": True, "path": _rel(path), "task": task or "all", "store": source_label}


def _project_specific_hits(novel_id: str, text: str) -> list[str]:
    hits = []
    if novel_id and novel_id in text:
        hits.append(novel_id)
    hits.extend(re.findall(r"第\s*[0-9一二三四五六七八九十百两]+\s*(场|幕|节拍|镜|镜头)", text))
    project_dir = novel_dir(novel_id)
    for path in sorted(project_dir.glob("*.md")):
        stem = path.stem
        if len(stem) >= 2 and stem in text:
            hits.append(stem)
    return sorted(set(hits))


def _compact_lesson(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("-") or stripped.startswith("1."):
            lines.append(stripped)
        if len(lines) >= 8:
            break
    return "\n".join(lines) or markdown[:800]


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip() or fallback
    return fallback


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
