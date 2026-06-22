from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import WRITING_ROOT, novel_dir, normalize_novel_id
from app.project_paths import skills_dir
from app.project_kinds import STRONG_NOVEL_KIND, project_kind


NOVEL_SKILL_SUITE = WRITING_ROOT / "novel-skill-suite"
SHARED_PUBLIC_SKILL = NOVEL_SKILL_SUITE / "shared" / "SKILL.md"

TASK_TO_SUITE_SKILLS = {
    "character": ["novel-writer-cn", "chinese-novelist", "novel-writer-lens"],
    "outline": ["novel-writer-cn", "novel-structure-extractor", "chinese-novelist"],
    "beat_sheet": ["novel-structure-extractor", "novel-writer-lens", "chinese-novelist"],
    "prose": ["novel-writer-cn", "novel-writer-lens", "chinese-novelist"],
    "expansion": ["novel-writer-cn", "novel-writer-lens", "chinese-novelist"],
    "fix": ["quality-review-checklist", "novel-writer-lens", "chinese-novelist"],
}
ALWAYS_RELEVANT = {"inkos-multi-agent-novel-writing", "shared"}

PROJECT_SPECIFIC_MARKERS = [
    "项目特有",
    "本项目",
    "当前项目",
    "本书",
    "本小说",
    "本章",
    "高中卷",
    "人物档案",
    "设定及问题",
    "穿越设定",
    "双线叙事",
]
GENERAL_SKILL_MARKERS = [
    "通用",
    "复用",
    "流程",
    "检查",
    "规范",
    "规则",
    "写作",
    "创作",
    "结构",
    "节奏",
    "角色",
    "对白",
    "场景",
    "冲突",
    "视角",
    "不要",
    "禁止",
    "必须",
    "应该",
]


def public_novel_skill_files() -> list[Path]:
    if not NOVEL_SKILL_SUITE.is_dir():
        return []
    return sorted(path for path in NOVEL_SKILL_SUITE.glob("*/SKILL.md") if path.is_file())


def project_skill_files(novel_id: str | None) -> list[Path]:
    root = skills_dir(novel_id)
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*.md") if path.is_file())


def skill_counts(novel_id: str | None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    public_files = public_novel_skill_files() if project_kind(nid) == STRONG_NOVEL_KIND else []
    project_files = project_skill_files(nid)
    return {
        "count": len(public_files) + len(project_files),
        "public_count": len(public_files),
        "project_count": len(project_files),
        "public_root": _rel(NOVEL_SKILL_SUITE) if NOVEL_SKILL_SUITE.exists() else "",
        "project_root": _rel(skills_dir(nid)),
        "files": [
            {"name": path.parent.name, "path": _rel(path), "source": "public_novel"}
            for path in public_files
        ] + [
            {"name": path.name, "path": _rel(path), "source": "project"}
            for path in project_files
        ],
    }


def registry_items(novel_id: str | None, task: str = "") -> list[dict[str, Any]]:
    nid = normalize_novel_id(novel_id)
    items = []
    if project_kind(nid) == STRONG_NOVEL_KIND:
        for path in public_novel_skill_files():
            text = _read(path)
            name = path.parent.name
            items.append(_item(path, name, text, "public_novel", _suite_relevant(name, task)))
    for path in project_skill_files(nid):
        text = _read(path)
        items.append(_item(path, path.name, text, "project", _project_relevant(path.name, task)))
    return items


def load_novel_skill_text(novel_id: str | None, task: str, max_chars: int = 7000) -> str:
    nid = normalize_novel_id(novel_id)
    if project_kind(nid) != STRONG_NOVEL_KIND:
        return ""
    parts = []
    remaining = max_chars
    for item in registry_items(nid, task):
        if not item.get("relevant"):
            continue
        path = ROOT / item["path"]
        text = _read(path)
        if not text:
            continue
        take = min(max(700, remaining // 3), remaining)
        excerpt = text[:take].strip()
        if not excerpt:
            continue
        parts.append(f"### {item['title']}（{item['source_label']}）\n{excerpt}")
        remaining -= len(excerpt)
        if remaining <= 500:
            break
    return "\n\n".join(parts)


def classify_skill_scope(novel_id: str | None, title: str, markdown: str, task: str = "") -> dict[str, Any]:
    """Decide whether a human-approved lesson belongs to the public novel skill suite or project skills."""
    nid = normalize_novel_id(novel_id)
    text = f"{title}\n{markdown}".strip()
    if project_kind(nid) != STRONG_NOVEL_KIND:
        return {
            "scope": "project",
            "reason": "非小说强类型项目暂无对应公共技能库，保留为项目私有技能。",
        }
    project_hits = _project_specific_hits(nid, text)
    general_hits = [marker for marker in GENERAL_SKILL_MARKERS if marker in text]
    if project_hits:
        return {
            "scope": "project",
            "reason": "包含项目专有线索，避免污染公共小说技能库。",
            "project_hits": project_hits[:8],
            "general_hits": general_hits[:8],
        }
    if len(general_hits) >= 2 or task in TASK_TO_SUITE_SKILLS:
        return {
            "scope": "public_novel",
            "reason": "未发现项目专有线索，且内容像可复用小说创作方法。",
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
    if decision["scope"] == "public_novel":
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
    SHARED_PUBLIC_SKILL.parent.mkdir(parents=True, exist_ok=True)
    header = "\n".join([
        "---",
        "name: writing-shared-lessons",
        "description: 人工确认后沉淀的通用中文小说创作经验，供所有小说类型项目复用。",
        "---",
        "",
        "# 小说公共经验技能",
        "",
    ])
    return _append_lesson_block(
        SHARED_PUBLIC_SKILL,
        header,
        title,
        markdown,
        task=task,
        source_path=source_path,
        source_label="public_novel_shared",
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


def _suite_relevant(name: str, task: str) -> bool:
    if not task:
        return True
    return name in set(TASK_TO_SUITE_SKILLS.get(task, [])) | ALWAYS_RELEVANT


def _project_relevant(name: str, task: str) -> bool:
    if not task:
        return True
    return name == "lessons_skill.md" or task in name


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
    hits.extend(marker for marker in PROJECT_SPECIFIC_MARKERS if marker in text)
    hits.extend(re.findall(r"第\s*[0-9一二三四五六七八九十百两]+\s*章", text))
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


def _item(path: Path, name: str, text: str, source: str, relevant: bool) -> dict[str, Any]:
    return {
        "name": name,
        "path": _rel(path),
        "title": _title(text, path.stem),
        "chars": len(text),
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "source": source,
        "source_label": "小说公共技能库" if source == "public_novel" else "项目沉淀技能",
        "relevant": relevant,
    }


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
