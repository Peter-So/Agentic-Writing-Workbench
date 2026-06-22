from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import normalize_novel_id
from app.project_paths import wiki_dir
from app.project_structure import load_project_structure


PROJECT_WIKI_CATEGORIES = {
    "state": "项目状态",
    "decision": "项目决定",
    "note": "过程备注",
    "todo": "待办",
    "material": "材料索引",
    "route": "路由说明",
}


def project_wiki_root(novel_id: str | None = None) -> Path:
    return wiki_dir(normalize_novel_id(novel_id))


def project_wiki_index_path(novel_id: str | None = None) -> Path:
    return project_wiki_root(novel_id) / "project_wiki.json"


def ensure_project_wiki(novel_id: str | None = None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    root = project_wiki_root(nid)
    root.mkdir(parents=True, exist_ok=True)
    index = project_wiki_index_path(nid)
    if not index.exists():
        _write_index(nid, {})
    readme = root / "README.md"
    if not readme.exists() or "project_wiki.json" not in readme.read_text(encoding="utf-8", errors="replace"):
        readme.write_text(_readme_text(), encoding="utf-8", newline="")
    _ensure_structure_note(nid)
    return {"ok": True, "novel_id": nid, "root": _rel(root), "index": _rel(index)}


def list_project_wiki(
    novel_id: str | None = None,
    *,
    limit: int = 100,
    category: str = "",
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    ensure_project_wiki(nid)
    items = list(_load_index(nid).values())
    if category:
        items = [item for item in items if item.get("category") == category]
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return {
        "ok": True,
        "novel_id": nid,
        "root": _rel(project_wiki_root(nid)),
        "summary": _summary(items),
        "items": items[:limit],
    }


def get_project_wiki_entry(novel_id: str | None, entry_id: str) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    ensure_project_wiki(nid)
    item = _load_index(nid).get(entry_id)
    if not item:
        raise KeyError("项目 Wiki 条目不存在")
    return {"ok": True, "novel_id": nid, "entry": {**item, "content": _read_content(ROOT / item["path"])}}


def upsert_project_wiki_entry(
    novel_id: str | None,
    *,
    title: str,
    content: str,
    category: str = "note",
    source: str = "",
    task: str = "",
    entry_id: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    ensure_project_wiki(nid)
    text = (content or "").strip()
    if len(text) < 2:
        raise ValueError("项目 Wiki 内容太短")
    clean_title = _clean_title(title or _title_from_text(text))
    cat = category if category in PROJECT_WIKI_CATEGORIES else "note"
    now = datetime.now().isoformat(timespec="seconds")
    existing = _load_index(nid)
    pid = entry_id.strip() if entry_id else _entry_id(cat, clean_title)
    old = existing.get(pid)
    path = project_wiki_root(nid) / f"project-{pid}.md"
    metadata = {
        "id": pid,
        "title": clean_title,
        "category": cat,
        "category_label": PROJECT_WIKI_CATEGORIES[cat],
        "task": task or "all",
        "source": source or "manual",
        "tags": _tags(clean_title, text, tags),
        "path": _rel(path),
        "created_at": (old or {}).get("created_at") or now,
        "updated_at": now,
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    }
    _write_entry(path, metadata, text)
    existing[pid] = metadata
    _write_index(nid, existing)
    return {"ok": True, "novel_id": nid, "entry": metadata, "created": old is None}


def delete_project_wiki_entry(novel_id: str | None, entry_id: str) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    ensure_project_wiki(nid)
    existing = _load_index(nid)
    item = existing.pop(entry_id, None)
    if not item:
        raise KeyError("项目 Wiki 条目不存在")
    path = ROOT / item.get("path", "")
    if path.exists() and str(path.resolve()).startswith(str(project_wiki_root(nid).resolve())):
        path.unlink()
    _write_index(nid, existing)
    return {"ok": True, "novel_id": nid, "deleted": entry_id}


def recall_project_wiki(
    novel_id: str | None,
    *,
    query: str = "",
    task: str = "",
    categories: list[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    nid = normalize_novel_id(novel_id)
    ensure_project_wiki(nid)
    index = _load_index(nid)
    if not index:
        return []
    terms = set(_terms(" ".join([query or "", task or ""])))
    allowed = set(categories or [])
    scored: list[dict[str, Any]] = []
    for item in index.values():
        if allowed and item.get("category") not in allowed:
            continue
        item_task = item.get("task") or "all"
        if task and item_task not in {"all", "通用", task}:
            continue
        content = _read_content(ROOT / item["path"])
        hay = " ".join([item.get("title", ""), " ".join(item.get("tags") or []), content])
        overlap = len(terms & set(_terms(hay))) if terms else 0
        if terms and overlap <= 0 and item_task not in {"all", "通用", task}:
            continue
        score = 0.5 + overlap * 0.15
        scored.append({**item, "content": content, "score": round(score, 3)})
    scored.sort(key=lambda item: (item["score"], item.get("updated_at", "")), reverse=True)
    return scored[:limit]


def format_project_wiki_for_prompt(items: list[dict[str, Any]], max_chars: int = 1800) -> str:
    blocks: list[str] = []
    used = 0
    for item in items or []:
        body = (item.get("content") or "").strip()
        if not body:
            continue
        block = (
            f"### 项目 Wiki·{item.get('category_label') or item.get('category')}·{item.get('title')}\n"
            f"[任务:{item.get('task') or 'all'}｜来源:{item.get('source') or ''}]\n"
            f"{body[:600]}"
        )
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def seed_project_wiki_from_structure(novel_id: str | None = None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    root = project_wiki_root(nid)
    root.mkdir(parents=True, exist_ok=True)
    if not project_wiki_index_path(nid).exists():
        _write_index(nid, {})
    structure = load_project_structure(nid)
    docs = structure.get("documents") or {}
    lines = [
        "项目级 Wiki 与结构索引的关系：",
        "- project-structure.json 负责规范目录、结构文件、别名和路由。",
        "- project_wiki.json 负责项目过程知识、当前状态、待办、材料备注和项目内决定。",
        "- LLM Wiki 负责人工确认后的稳定规则、跨轮共识、设定和经验，会作为高权威规则注入。",
        "",
        "当前结构文件：",
    ]
    for item in docs.values():
        lines.append(f"- {item.get('label')}：{item.get('path')}；{item.get('description')}")
    entry = upsert_project_wiki_entry(
        nid,
        entry_id="structure-map",
        title="项目结构与 Wiki 分工",
        content="\n".join(lines),
        category="route",
        source="project_structure",
        task="all",
        tags=["项目结构", "Wiki", "路由"],
    )
    return {"ok": True, "novel_id": nid, "entry": entry.get("entry")}


def _ensure_structure_note(novel_id: str) -> None:
    if "structure-map" not in _load_index(novel_id):
        try:
            seed_project_wiki_from_structure(novel_id)
        except Exception:
            pass


def _load_index(novel_id: str) -> dict[str, dict[str, Any]]:
    path = project_wiki_index_path(novel_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_index(novel_id: str, data: dict[str, dict[str, Any]]) -> None:
    path = project_wiki_index_path(novel_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _write_entry(path: Path, metadata: dict[str, Any], content: str) -> None:
    front = json.dumps(metadata, ensure_ascii=False, indent=2)
    text = f"```json\n{front}\n```\n\n# {metadata['title']}\n\n{content.strip()}\n"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def _read_content(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = re.sub(r"^```json\s*\{.*?\}\s*```\s*", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^# .+?\n+", "", text).strip()
    return text


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, int] = {}
    for item in items:
        cat = item.get("category_label") or item.get("category") or "未知"
        by_category[cat] = by_category.get(cat, 0) + 1
    return {"total": len(items), "by_category": by_category}


def _entry_id(category: str, title: str) -> str:
    seed = f"{category}:{title}".encode("utf-8", errors="ignore")
    return "PW-" + hashlib.sha256(seed).hexdigest()[:14]


def _clean_title(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    return value[:80] or "项目备注"


def _title_from_text(text: str) -> str:
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else "项目备注"
    return re.sub(r"^[#\-*\s]+", "", first)[:60] or "项目备注"


def _tags(title: str, text: str, extra: list[str] | None = None) -> list[str]:
    terms = _terms(" ".join([title, text]))
    out: list[str] = []
    for item in list(extra or []) + terms:
        if item and item not in out:
            out.append(item)
        if len(out) >= 12:
            break
    return out


def _terms(text: str) -> list[str]:
    stop = {"这个", "我们", "用户", "输出", "项目", "任务", "材料", "需要", "不能", "不要", "必须", "进行"}
    terms = re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,8}", text or "")
    return [term.lower() for term in terms if term not in stop]


def _readme_text() -> str:
    return (
        "# 项目 Wiki\n\n"
        "本目录同时包含三类 Wiki 文件：\n\n"
        "- `project-structure.json` / `项目结构.md`：结构索引，负责规范目录、文件角色、别名和路由。\n"
        "- `project_wiki.json` / `project-*.md`：项目级动态 Wiki，负责项目状态、过程备注、待办、材料索引和项目内决定。\n"
        "- `index.json` / `WK-*.md`：LLM Wiki，负责人工确认后的稳定规则、设定、共识和经验，权威高于普通材料。\n\n"
        "创作流程会读取项目级动态 Wiki 和 LLM Wiki；结构索引用于定位文件，不直接替代正文材料。\n"
    )


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
