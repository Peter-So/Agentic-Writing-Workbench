from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import normalize_novel_id
from app.project_kinds import SHORT_FILM_KIND, STRONG_NOVEL_KIND, project_kind
from app.project_paths import skills_dir


TASK_SKILL_HINTS = {
    "logline": ["screenplay-skill.md"],
    "screenplay": ["screenplay-skill.md"],
    "character": ["screenplay-skill.md", "character-visual-skill.md"],
    "beat_sheet": ["screenplay-skill.md"],
    "shot_list": ["storyboard-skill.md", "image-consistency-style-guide.md"],
    "fix": ["screenplay-skill.md"],
}
GLOBAL_RECOMMENDED = ["lessons-skill.md"]


def skills_registry(novel_id: str, task: str = "") -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    root = skills_dir(nid)
    kind = project_kind(nid)
    if kind == STRONG_NOVEL_KIND:
        from app.novel_skills import NOVEL_SKILL_SUITE, registry_items

        files = registry_items(nid, task)
        recommended = [item["name"] for item in files if item.get("source") == "public_novel" and item.get("relevant")]
        recommended += GLOBAL_RECOMMENDED
        root_label = f"{_rel(NOVEL_SKILL_SUITE)} + {_rel(root)}"
    elif kind == SHORT_FILM_KIND:
        from app.short_film_skill_store import SHORT_FILM_SKILL_SUITE, registry_items

        files = registry_items(nid, task)
        recommended = [item["name"] for item in files if item.get("source") == "public_short_film" and item.get("relevant")]
        recommended += GLOBAL_RECOMMENDED
        root_label = f"{_rel(SHORT_FILM_SKILL_SUITE)} + {_rel(root)}"
    else:
        files = []
        if root.is_dir():
            for path in sorted(root.glob("*.md")):
                text = path.read_text(encoding="utf-8", errors="replace")
                files.append({
                    "name": path.name,
                    "path": _rel(path),
                    "title": _title(text, path.stem),
                    "chars": len(text),
                    "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
                    "source": "project",
                    "source_label": "项目沉淀技能",
                    "relevant": _relevant(path.name, task),
                })
        recommended = TASK_SKILL_HINTS.get(task, []) + GLOBAL_RECOMMENDED
        root_label = _rel(root)
    existing = {item["name"] for item in files}
    missing = [name for name in recommended if name not in existing]
    return {
        "ok": True,
        "novel_id": nid,
        "task": task,
        "root": root_label,
        "files": files,
        "load_policy": {
            "default": "load_relevant_task_cards_only",
            "relevant_count": sum(1 for item in files if item.get("relevant")),
        },
        "recommended": recommended,
        "missing_recommended": missing,
    }


def _relevant(name: str, task: str) -> bool:
    if not task:
        return True
    return name in TASK_SKILL_HINTS.get(task, []) or name in GLOBAL_RECOMMENDED


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip() or fallback
    return fallback


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
