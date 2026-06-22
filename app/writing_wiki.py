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


WIKI_CATEGORIES = {
    "rule": "规则",
    "consensus": "共识",
    "setting": "设定",
    "character": "角色",
    "style": "风格",
    "lesson": "经验",
}

AUTHORITY = {
    "human_confirmed": 90,
    "confirmed_setting": 82,
    "lesson": 72,
    "summary": 68,
    "draft": 35,
}


def wiki_root(novel_id: str | None = None) -> Path:
    return wiki_dir(normalize_novel_id(novel_id))


def wiki_index_path(novel_id: str | None = None) -> Path:
    return wiki_root(novel_id) / "index.json"


def ensure_wiki(novel_id: str | None = None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    root = wiki_root(nid)
    root.mkdir(parents=True, exist_ok=True)
    index = wiki_index_path(nid)
    if not index.exists():
        _write_index(nid, {})
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            "# LLM Wiki\n\n"
            "本目录保存人工确认后的稳定规则、项目共识、设定和经验。"
            "这些条目会在材料组装时优先注入 prompt，权威高于普通聊天和临时草稿。\n",
            encoding="utf-8",
            newline="",
        )
    return {"ok": True, "novel_id": nid, "root": _rel(root), "index": _rel(index)}


def list_wiki(novel_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    ensure_wiki(nid)
    items = list(_load_index(nid).values())
    items.sort(key=lambda item: (int(item.get("authority_score") or 0), item.get("updated_at", "")), reverse=True)
    return {
        "ok": True,
        "novel_id": nid,
        "root": _rel(wiki_root(nid)),
        "summary": _summary(items),
        "items": items[:limit],
    }


def adopt_wiki_entry(
    novel_id: str | None,
    *,
    title: str,
    content: str,
    category: str = "consensus",
    source: str = "",
    task: str = "",
    authority: str = "human_confirmed",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    ensure_wiki(nid)
    text = (content or "").strip()
    clean_title = _clean_title(title or _title_from_text(text))
    if len(text) < 8:
        raise ValueError("Wiki 内容太短")
    cat = category if category in WIKI_CATEGORIES else "consensus"
    auth = authority if authority in AUTHORITY else "human_confirmed"
    now = datetime.now().isoformat(timespec="seconds")
    wid = _entry_id(clean_title, text)
    path = wiki_root(nid) / f"{wid}.md"
    metadata = {
        "id": wid,
        "title": clean_title,
        "category": cat,
        "category_label": WIKI_CATEGORIES[cat],
        "authority": auth,
        "authority_score": AUTHORITY[auth],
        "task": task or "all",
        "source": source or "manual",
        "tags": _tags(clean_title, text, tags),
        "path": _rel(path),
        "created_at": now,
        "updated_at": now,
        "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    }
    existing = _load_index(nid)
    old = existing.get(wid)
    if old:
        metadata["created_at"] = old.get("created_at") or metadata["created_at"]
    _write_entry(path, metadata, text)
    existing[wid] = metadata
    _write_index(nid, existing)
    return {"ok": True, "novel_id": nid, "entry": metadata, "created": old is None}


def recall_wiki(
    novel_id: str | None,
    *,
    query: str = "",
    task: str = "",
    categories: list[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    nid = normalize_novel_id(novel_id)
    ensure_wiki(nid)
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
        base = int(item.get("authority_score") or 0) / 100
        score = base + overlap * 0.12
        if terms and overlap <= 0 and item_task not in {"all", "通用", task}:
            continue
        scored.append({**item, "content": content, "score": round(score, 3)})
    scored.sort(key=lambda item: (item["score"], item.get("updated_at", "")), reverse=True)
    return scored[:limit]


def format_wiki_for_prompt(items: list[dict[str, Any]], max_chars: int = 2400) -> str:
    if not items:
        return ""
    blocks: list[str] = []
    used = 0
    for item in items:
        body = (item.get("content") or "").strip()
        if not body:
            continue
        block = (
            f"### LLM Wiki·{item.get('category_label') or item.get('category')}·{item.get('title')}\n"
            f"[权威:{item.get('authority_score')}｜任务:{item.get('task') or 'all'}｜来源:{item.get('source') or ''}]\n"
            f"{body[:700]}"
        )
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def seed_wiki_from_existing(novel_id: str | None = None) -> dict[str, Any]:
    """Create wiki entries from already-confirmed project memory. Idempotent."""
    nid = normalize_novel_id(novel_id)
    created = []
    try:
        from app.writing_memory import load_settings

        for track in ("create", "normal"):
            for setting in load_settings(track, project=nid):
                value = setting.get("value") or {}
                if isinstance(value, dict):
                    text = value.get("content") or value.get("text") or json.dumps(value, ensure_ascii=False)
                    title = value.get("title") or setting.get("key") or "长期设定"
                else:
                    text = str(value)
                    title = setting.get("key") or "长期设定"
                if text and len(text.strip()) >= 8:
                    created.append(adopt_wiki_entry(
                        nid,
                        title=str(title),
                        content=str(text)[:3000],
                        category="setting",
                        source=f"store:{track}:{setting.get('key', '')}",
                        task=str((value or {}).get("task") or "all") if isinstance(value, dict) else "all",
                        authority="confirmed_setting",
                    ))
    except Exception:
        pass
    try:
        from app.writing_lessons import list_lessons

        for lesson in list_lessons(limit=100).get("items") or []:
            path = ROOT / lesson.get("path", "")
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            if text.strip():
                created.append(adopt_wiki_entry(
                    nid,
                    title=lesson.get("title") or path.stem,
                    content=text[:3000],
                    category="lesson",
                    source=lesson.get("path") or "lessons",
                    authority="lesson",
                ))
    except Exception:
        pass
    return {"ok": True, "novel_id": nid, "created_or_updated": len(created), "items": [x.get("entry") for x in created]}


def _load_index(novel_id: str) -> dict[str, dict[str, Any]]:
    path = wiki_index_path(novel_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_index(novel_id: str, data: dict[str, dict[str, Any]]) -> None:
    path = wiki_index_path(novel_id)
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
    return {
        "total": len(items),
        "high_authority": sum(1 for item in items if int(item.get("authority_score") or 0) >= 80),
        "by_category": by_category,
    }


def _entry_id(title: str, content: str) -> str:
    seed = f"{title}\n{content}".encode("utf-8", errors="ignore")
    return "WK-" + hashlib.sha256(seed).hexdigest()[:16]


def _clean_title(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    return value[:80] or "项目共识"


def _title_from_text(text: str) -> str:
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else "项目共识"
    return re.sub(r"^[#\-*\s]+", "", first)[:60] or "项目共识"


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


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
