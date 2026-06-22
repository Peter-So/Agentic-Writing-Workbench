from __future__ import annotations

import re
from pathlib import Path

from app.config import ROOT

WRITING_ROOT = ROOT / "projects" / "writing"
NOVELS_ROOT = WRITING_ROOT / "novels"
DEFAULT_NOVEL_ID = "001"


def normalize_novel_id(novel_id: str | None) -> str:
    value = (novel_id or DEFAULT_NOVEL_ID).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value):
        raise ValueError("非法项目编号")
    return value


def novel_dir(novel_id: str | None = None) -> Path:
    nid = normalize_novel_id(novel_id)
    path = (NOVELS_ROOT / nid).resolve()
    root = NOVELS_ROOT.resolve()
    if not str(path).startswith(str(root)):
        raise ValueError("项目路径越界")
    return path


def novel_id_from_path(path: Path) -> str:
    resolved = path.resolve()
    root = NOVELS_ROOT.resolve()
    if not str(resolved).startswith(str(root)):
        return DEFAULT_NOVEL_ID
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return DEFAULT_NOVEL_ID
    return rel.parts[0] if rel.parts else DEFAULT_NOVEL_ID


def list_novels() -> list[dict[str, str]]:
    if not NOVELS_ROOT.exists():
        return []
    items: list[dict[str, str]] = []
    for path in sorted((p for p in NOVELS_ROOT.iterdir() if p.is_dir()), key=lambda p: p.name):
        label = path.name
        kind = ""
        project_file = path / "project.yaml"
        if project_file.exists():
            try:
                for line in project_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("type:") or stripped.startswith("kind:"):
                        kind = stripped.split(":", 1)[1].strip().strip("'\"") or kind
                    elif stripped.startswith("name:"):
                        label = stripped.split(":", 1)[1].strip().strip("'\"") or label
            except Exception:
                pass
        if not kind:
            has_assembler = (
                (path / "脚本" / "material_assembler.py").exists()
                or (path / "scripts" / "material_assembler.py").exists()
            )
            has_prompts = (path / "提示词").exists() or (path / "prompts").exists()
            if has_assembler and has_prompts:
                kind = "novel_strong"
            elif (
                (path / "剧本" / "剧本.md").exists()
                or (path / "开发" / "节拍表.md").exists()
                or (path / "screenplay.md").exists()
                or (path / "script.md").exists()
            ):
                kind = "short_film"
            else:
                kind = "generic"
        items.append({
            "id": path.name,
            "name": label,
            "kind": kind,
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        })
    return items
