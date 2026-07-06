from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import WRITING_ROOT, normalize_novel_id, novel_dir
from app.project_kinds import project_kind
from app.project_paths import assets_dir, project_dir, wiki_dir


REGISTRY_NAME = "entity_registry.json"
TEXT_SUFFIXES = {".md", ".txt", ".json"}


def build_entity_registry(novel_id: str | None, *, persist: bool = True) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    root = novel_dir(nid)
    kind = project_kind(nid)
    items: list[dict[str, Any]] = []
    structure = _load_project_structure(nid)
    items.extend(_items_from_structure(structure))
    items.extend(_items_from_project_dirs(nid))
    items.extend(_items_from_wiki(nid))
    items.extend(_reference_dimension_items())
    items = _dedupe(items)
    registry = {
        "ok": True,
        "version": 1,
        "novel_id": nid,
        "project_kind": kind,
        "root": _rel(root),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": _summary(items),
        "items": items,
    }
    if persist:
        target = wiki_dir(nid) / REGISTRY_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return registry


def load_entity_registry(
    novel_id: str | None,
    *,
    rebuild: bool = False,
    persist: bool = False,
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    target = wiki_dir(nid) / REGISTRY_NAME
    if rebuild:
        return build_entity_registry(nid, persist=True)
    if not target.is_file():
        return build_entity_registry(nid, persist=persist)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == 1:
            return data
    except Exception:
        pass
    return build_entity_registry(nid, persist=persist)


def registry_summary(novel_id: str | None) -> dict[str, Any]:
    target = wiki_dir(novel_id) / REGISTRY_NAME
    if target.is_file():
        data = load_entity_registry(novel_id)
    else:
        data = build_entity_registry(novel_id, persist=False)
    return {
        "ok": bool(data.get("ok")),
        "updated_at": data.get("updated_at") or "",
        "summary": data.get("summary") or {},
        "path": _rel(wiki_dir(novel_id) / REGISTRY_NAME),
    }


def _items_from_structure(structure: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    paths = structure.get("paths") if isinstance(structure, dict) else {}
    if isinstance(paths, dict):
        for key, spec in paths.items():
            if not isinstance(spec, dict):
                continue
            path = str(spec.get("path") or "").strip()
            if not path:
                continue
            items.append(_item(
                entity_type=_role_to_type(str(spec.get("role") or key)),
                name=str(spec.get("label") or key),
                path=path,
                role=str(spec.get("role") or key),
                source="project_structure",
                aliases=spec.get("aliases") if isinstance(spec.get("aliases"), list) else [],
            ))
    return items


def _items_from_project_dirs(novel_id: str) -> list[dict[str, Any]]:
    specs = [
        ("chapter", project_dir(novel_id, "chapters"), "chapter_body"),
        ("character", project_dir(novel_id, "characters"), "character"),
        ("setting", project_dir(novel_id, "settings"), "setting"),
        ("outline", project_dir(novel_id, "planning"), "outline"),
        ("asset", assets_dir(novel_id), "asset"),
        ("reference", project_dir(novel_id, "references"), "reference"),
        ("storyboard", project_dir(novel_id, "storyboards"), "storyboard"),
    ]
    items: list[dict[str, Any]] = []
    for entity_type, root, role in specs:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            items.append(_item(
                entity_type=entity_type,
                name=_name_from_path(path),
                path=_rel(path),
                role=role,
                source="project_dir_scan",
                chapter=_chapter_from_name(path.name),
            ))
    return items


def _items_from_wiki(novel_id: str) -> list[dict[str, Any]]:
    root = wiki_dir(novel_id)
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md")):
        text = _safe_read(path, limit=3000)
        entity_type = _wiki_type(path.name, text)
        items.append(_item(
            entity_type=entity_type,
            name=_name_from_path(path),
            path=_rel(path),
            role=entity_type,
            source="project_wiki",
            aliases=_aliases_from_text(text),
        ))
    return items


def _reference_dimension_items() -> list[dict[str, Any]]:
    extracted = WRITING_ROOT / "novel-acquisition" / "extracted"
    if not extracted.is_dir():
        return []
    counts: dict[str, int] = {}
    for path in sorted(extracted.glob("*/anchor_analysis.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for anchor in data.get("results") or []:
            dimensions = anchor.get("dimensions") if isinstance(anchor, dict) else {}
            if not isinstance(dimensions, dict):
                continue
            for name, entries in dimensions.items():
                counts[name] = counts.get(name, 0) + (len(entries) if isinstance(entries, list) else 0)
    return [
        _item(
            entity_type="reference_dimension",
            name=name,
            path="projects/writing/novel-acquisition/extracted",
            role="reference_dimension",
            source="reference_library",
            meta={"segment_count": count},
        )
        for name, count in sorted(counts.items())
    ]


def _load_project_structure(novel_id: str) -> dict[str, Any]:
    for name in ("project_structure.json", "project-structure.json"):
        path = wiki_dir(novel_id) / name
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
    return {}


def _item(
    *,
    entity_type: str,
    name: str,
    path: str,
    role: str,
    source: str,
    aliases: list[Any] | None = None,
    chapter: int | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = {
        "id": _stable_id(entity_type, path, name),
        "type": entity_type,
        "name": name,
        "path": path,
        "role": role,
        "source": source,
        "aliases": [str(item)[:80] for item in (aliases or []) if str(item).strip()][:12],
        "meta": meta or {},
    }
    if chapter:
        data["chapter"] = chapter
    return data


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    for item in items:
        key = str(item.get("type") or "unknown")
        by_type[key] = by_type.get(key, 0) + 1
    return {
        "total": len(items),
        "by_type": dict(sorted(by_type.items())),
    }


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("id") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _role_to_type(role: str) -> str:
    text = role.strip().lower()
    if "chapter" in text or "正文" in text:
        return "chapter"
    if "character" in text or "人物" in text:
        return "character"
    if "world" in text or "世界" in text:
        return "worldview"
    if "outline" in text or "大纲" in text:
        return "outline"
    if "asset" in text or "资产" in text:
        return "asset"
    if "reference" in text or "参考" in text:
        return "reference"
    return text or "entity"


def _wiki_type(name: str, text: str) -> str:
    target = f"{name}\n{text[:800]}".lower()
    if "人物" in target or "character" in target:
        return "character"
    if "世界观" in target or "world" in target:
        return "worldview"
    if "大纲" in target or "outline" in target:
        return "outline"
    if "设定" in target or "setting" in target:
        return "setting"
    if "资产" in target or "asset" in target:
        return "asset"
    if "章节" in target or "chapter" in target:
        return "chapter"
    return "wiki"


def _chapter_from_name(name: str) -> int | None:
    match = re.search(r"(?:chapter|ch|第)?[-_\s]*0*(\d{1,4})", name, flags=re.I)
    if not match:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def _aliases_from_text(text: str) -> list[str]:
    aliases: list[str] = []
    for line in text.splitlines()[:40]:
        clean = line.strip(" #*-：:\t")
        if 2 <= len(clean) <= 40 and any(mark in line for mark in ("#", "名称", "别名", "标题")):
            aliases.append(clean)
    return aliases[:8]


def _name_from_path(path: Path) -> str:
    return path.stem.replace("_", "-")


def _safe_read(path: Path, *, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _stable_id(*parts: str) -> str:
    import hashlib

    raw = "\n".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _rel(path: Path | str) -> str:
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")
