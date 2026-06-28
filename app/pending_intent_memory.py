from __future__ import annotations

import hashlib
import json
import os
import re
from difflib import SequenceMatcher
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import novel_dir, normalize_novel_id


MEMORY_ROOT = ROOT / "data" / "writing_intent_memory"
SHORT_FILE = MEMORY_ROOT / "short_term_pending.json"
LONG_FILE = MEMORY_ROOT / "long_term_pending.json"
TTL_HOURS = int(os.getenv("PENDING_INTENT_TTL_HOURS", "24") or "24")

CN_NUMS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def save_pending_intent(
    *,
    novel_id: str | None,
    track: str,
    invocation_id: str | None,
    message: str,
    analysis: dict[str, Any],
    task: str,
    chapter: int | None,
    project_kind: str | None = None,
) -> dict[str, Any]:
    """Save the latest unfinished creative intent as short-term recoverable memory."""
    nid = normalize_novel_id(novel_id)
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    promote_expired_short_term(nid, track)
    now = _now()
    effective_task = str((analysis or {}).get("task") or task or "prose")
    effective_chapter = _clean_int((analysis or {}).get("target_chapter")) or chapter
    data = _load(SHORT_FILE)
    existing = _find_message_record(data, nid, track, message)
    record_id = (existing or {}).get("id") or invocation_id or _record_id(nid, track, message, now)
    record = {
        "id": record_id,
        "novel_id": nid,
        "track": _track(track),
        "status": "pending",
        "invocation_id": invocation_id or "",
        "message": (message or "").strip(),
        "message_hash": _hash(message or ""),
        "analysis": analysis or {},
        "task": effective_task,
        "chapter": effective_chapter,
        "project_kind": project_kind or "",
        "related_files": related_file_context(
            novel_id=nid,
            analysis=analysis or {},
            task=effective_task,
            chapter=effective_chapter,
        ),
        "involved_characters": _clean_list((analysis or {}).get("involved_characters")),
        "plot_points": _clean_list((analysis or {}).get("plot_points")),
        "target_sections": _clean_list((analysis or {}).get("target_sections")),
        "created_at": (existing or {}).get("created_at") or now,
        "updated_at": now,
        "expires_at": (datetime.now() + timedelta(hours=TTL_HOURS)).isoformat(timespec="seconds"),
    }
    data.setdefault("items", {})[record_id] = record
    data.setdefault("latest", {})[_latest_key(nid, track)] = record_id
    _write(SHORT_FILE, data)
    return record


def recover_pending_intent(
    *,
    novel_id: str | None,
    track: str,
    invocation_id: str | None = "",
) -> dict[str, Any] | None:
    """Recover pending intent: short-term first, then long-term, then invocation log."""
    nid = normalize_novel_id(novel_id)
    promote_expired_short_term(nid, track)
    for source, path in (("short", SHORT_FILE), ("long", LONG_FILE)):
        data = _load(path)
        item = _find_record(data, nid, track, invocation_id)
        if item:
            return {**item, "memory_source": source}
    log_item = _recover_from_invocation(nid, invocation_id)
    if log_item:
        return {**log_item, "memory_source": "invocation_log"}
    return None


def recover_pending_intent_by_message(
    *,
    novel_id: str | None,
    track: str,
    message: str,
    min_similarity: float | None = None,
) -> dict[str, Any] | None:
    """Recover an unfinished intent for a repeated user message before calling LLM.

    Exact hashes and normalized-equal messages always match. Similarity matching is
    deliberately strict so a related but newly scoped request still receives fresh
    LLM analysis.
    """
    nid = normalize_novel_id(novel_id)
    if not str(message or "").strip():
        return None
    promote_expired_short_term(nid, track)
    threshold = min_similarity if min_similarity is not None else float(
        os.getenv("PENDING_INTENT_MESSAGE_SIMILARITY", "0.96") or "0.96"
    )
    ranked: list[tuple[int, float, str, dict[str, Any]]] = []
    for priority, (source, path) in enumerate((("short", SHORT_FILE), ("long", LONG_FILE))):
        data = _load(path)
        for item in (data.get("items") or {}).values():
            if item.get("novel_id") != nid or item.get("track") != _track(track):
                continue
            if item.get("status") not in {"pending", "stale_pending"}:
                continue
            if not isinstance(item.get("analysis"), dict) or not item.get("analysis"):
                continue
            if item.get("message_hash") == _hash(message or ""):
                score, match_kind = 1.0, "hash"
            else:
                score, match_kind = _message_match(message, item.get("message") or "")
            if score < threshold and match_kind not in {"hash", "normalized"}:
                continue
            ranked.append((priority, score, str(item.get("updated_at") or ""), {
                **item,
                "memory_source": source,
                "message_match_score": round(score, 4),
                "message_match_kind": match_kind,
            }))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[2], reverse=True)
    ranked.sort(key=lambda row: row[1], reverse=True)
    ranked.sort(key=lambda row: row[0])
    return ranked[0][3]


def complete_pending_intent(
    *,
    novel_id: str | None,
    track: str,
    invocation_id: str | None = "",
    status: str = "completed",
) -> dict[str, Any]:
    """Clear short-term pending memory after user confirmation/rejection/archive."""
    nid = normalize_novel_id(novel_id)
    data = _load(SHORT_FILE)
    item = _find_record(data, nid, track, invocation_id)
    if not item:
        return {"ok": True, "cleared": False}
    item = {**item, "status": status, "completed_at": _now(), "updated_at": _now()}
    _store_long(item)
    data.get("items", {}).pop(item["id"], None)
    latest_key = _latest_key(nid, track)
    if data.get("latest", {}).get(latest_key) == item["id"]:
        data["latest"].pop(latest_key, None)
    _write(SHORT_FILE, data)
    return {"ok": True, "cleared": True, "id": item["id"], "status": status}


def update_pending_workflow_status(
    *,
    novel_id: str | None,
    track: str,
    invocation_id: str | None = "",
    workflow_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a recoverable UI workflow/status-bar snapshot to pending intent."""
    nid = normalize_novel_id(novel_id)
    data = _load(SHORT_FILE)
    item = _find_record(data, nid, track, invocation_id)
    if not item:
        return {"ok": True, "updated": False, "reason": "pending_intent_not_found"}
    status = _clean_workflow_status(workflow_status or {})
    if not status:
        return {"ok": False, "updated": False, "error": "empty_workflow_status"}
    status.setdefault("invocation_id", invocation_id or item.get("invocation_id") or item.get("id") or "")
    status.setdefault("updated_at", _now())
    item["workflow_status"] = status
    item["updated_at"] = _now()
    data.setdefault("items", {})[item["id"]] = item
    data.setdefault("latest", {})[_latest_key(nid, track)] = item["id"]
    _write(SHORT_FILE, data)
    return {"ok": True, "updated": True, "id": item["id"], "workflow_status": status}


def latest_pending_workflow_status(
    *,
    novel_id: str | None,
    track: str,
    invocation_id: str | None = "",
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    promote_expired_short_term(nid, track)
    item = _find_record(_load(SHORT_FILE), nid, track, invocation_id)
    if not item:
        return {"ok": True, "found": False}
    return {
        "ok": True,
        "found": True,
        "pending_intent": item,
        "workflow_status": item.get("workflow_status") or {},
    }


def promote_expired_short_term(novel_id: str | None = None, track: str | None = None) -> dict[str, Any]:
    data = _load(SHORT_FILE)
    items = data.get("items") or {}
    now = datetime.now()
    moved: list[str] = []
    for item_id, item in list(items.items()):
        if novel_id and item.get("novel_id") != normalize_novel_id(novel_id):
            continue
        if track and item.get("track") != _track(track):
            continue
        expires_at = _parse_time(item.get("expires_at"))
        if expires_at and expires_at > now:
            continue
        moved_item = {**item, "status": "stale_pending", "promoted_at": _now(), "updated_at": _now()}
        _store_long(moved_item)
        items.pop(item_id, None)
        moved.append(item_id)
    if moved:
        latest = data.get("latest") or {}
        for key, item_id in list(latest.items()):
            if item_id in moved:
                latest.pop(key, None)
        data["latest"] = latest
        data["items"] = items
        _write(SHORT_FILE, data)
    return {"ok": True, "moved": moved}


def related_file_context(
    *,
    novel_id: str | None,
    analysis: dict[str, Any],
    task: str,
    chapter: int | None,
) -> list[dict[str, Any]]:
    nid = normalize_novel_id(novel_id)
    targets = _target_names(nid, analysis, task)
    refs: list[dict[str, Any]] = []
    prose_refs = _prose_refs(nid, analysis, task, chapter)
    for target in targets:
        if _is_chapter_target(target):
            refs.extend(prose_refs or _chapter_refs(nid, chapter))
            continue
        resolved = _resolve_target(nid, target)
        if not resolved:
            continue
        role, path = resolved
        refs.append(_file_ref(path, role=role, target=target, chapter=chapter, analysis=analysis))
    if prose_refs and not any(ref.get("role") == "chapter_body" for ref in refs):
        refs.extend(prose_refs)
    unique: dict[str, dict[str, Any]] = {}
    for ref in refs:
        key = f"{ref.get('path')}:{ref.get('start_line')}:{ref.get('end_line')}"
        unique[key] = ref
    return list(unique.values())


def merge_recovered_context(
    *,
    request_analysis: dict[str, Any],
    task: str,
    chapter: int | None,
    recovered: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, int | None]:
    if not recovered:
        return request_analysis or {}, task, chapter
    analysis = dict(request_analysis or {})
    analysis.update(recovered.get("analysis") or {})
    if recovered.get("related_files") and not analysis.get("related_files"):
        analysis["related_files"] = recovered.get("related_files")
    effective_task = str(analysis.get("task") or recovered.get("task") or task)
    effective_chapter = _clean_int(analysis.get("target_chapter")) or recovered.get("chapter") or chapter
    if effective_chapter:
        analysis["target_chapter"] = effective_chapter
    if effective_task:
        analysis["task"] = effective_task
    analysis.setdefault("recovered_from", recovered.get("memory_source") or "pending_intent")
    return analysis, effective_task, effective_chapter


def _target_names(novel_id: str, analysis: dict[str, Any], task: str) -> list[str]:
    names = _clean_list(analysis.get("affected_files"))
    material_roles = _clean_list(analysis.get("affected_materials"))
    try:
        from app.project_structure import load_project_structure, role_for_task

        structure = load_project_structure(novel_id)
        kind = structure.get("project_kind")
        docs = structure.get("documents") or {}
        for role in material_roles:
            spec = docs.get(role)
            if spec:
                names.append(spec.get("path") or spec.get("label") or role)
        default_role = role_for_task(task, kind)
        default = ""
        if default_role and docs.get(default_role):
            spec = docs[default_role]
            default = spec.get("path") or spec.get("label") or default_role
    except Exception:
        default = ""
    if default and default not in names:
        names.insert(0, default)
    return names[:12]


def _is_chapter_target(target: str) -> bool:
    text = str(target or "")
    return text in {"章节正文", "正文"} or "正文/chapter" in text or re.search(r"chapter-\d+", text, re.IGNORECASE) is not None


def _resolve_target(novel_id: str, target: str) -> tuple[str, Path] | None:
    try:
        from app.project_structure import find_related_structure_file, resolve_structure_target

        role, path = resolve_structure_target(novel_id, target, create_missing=False)
        if path and path.exists():
            return role or "", path
        matched = find_related_structure_file(novel_id, target)
        if matched:
            return matched
    except Exception:
        return None
    return None


def _chapter_refs(novel_id: str, chapter: int | None) -> list[dict[str, Any]]:
    if not chapter:
        return []
    base = novel_dir(novel_id)
    candidates = sorted((base / "正文").glob(f"chapter-{chapter:02d}*.md"))
    refs = []
    for path in candidates[:3]:
        refs.append(_file_ref(path, role="prose", target="章节正文", chapter=chapter, analysis={}))
    return refs


def _prose_refs(novel_id: str, analysis: dict[str, Any], task: str, chapter: int | None) -> list[dict[str, Any]]:
    locations = analysis.get("prose_locations") if isinstance(analysis, dict) else []
    if isinstance(locations, list) and locations:
        return [dict(item) for item in locations if isinstance(item, dict)]
    try:
        from app.prose_locator import is_prose_refinement_intent, locate_prose_targets

        if not is_prose_refinement_intent(analysis, task):
            return []
        return locate_prose_targets(
            novel_id=novel_id,
            chapter=chapter,
            analysis=analysis,
        )
    except Exception:
        return []


def _file_ref(path: Path, *, role: str, target: str, chapter: int | None, analysis: dict[str, Any]) -> dict[str, Any]:
    rel = _rel(path)
    text = _read_text(path)
    start, end, matched = _line_span(text, chapter, analysis)
    return {
        "role": role,
        "target": target,
        "path": rel,
        "exists": path.exists(),
        "start_line": start,
        "end_line": end,
        "matched": matched,
    }


def _line_span(text: str, chapter: int | None, analysis: dict[str, Any]) -> tuple[int | None, int | None, str]:
    lines = text.splitlines()
    if not lines:
        return None, None, ""
    if chapter:
        cn = next((k for k, v in CN_NUMS.items() if v == chapter and k != "两"), str(chapter))
        patterns = [
            rf"^#{{1,6}}\s*第\s*{chapter}\s*章\b",
            rf"^#{{1,6}}\s*第\s*{cn}\s*章\b",
            rf"^#{{1,6}}\s*(Ch|Chapter)\s*{chapter}\b",
        ]
        for i, line in enumerate(lines):
            if any(re.search(pattern, line, re.IGNORECASE) for pattern in patterns):
                level = len(line) - len(line.lstrip("#"))
                end = len(lines)
                for j in range(i + 1, len(lines)):
                    if re.match(r"^#{1,6}\s+", lines[j]):
                        next_level = len(lines[j]) - len(lines[j].lstrip("#"))
                        if next_level <= level:
                            end = j
                            break
                return i + 1, end, f"chapter:{chapter}"
    terms = _clean_list(analysis.get("target_sections")) + _clean_list(analysis.get("involved_characters"))
    for term in terms:
        for i, line in enumerate(lines):
            if term and term in line:
                return i + 1, min(len(lines), i + 5), term
    return 1, min(len(lines), 20), "file_head"


def _recover_from_invocation(novel_id: str, invocation_id: str | None) -> dict[str, Any] | None:
    if not invocation_id:
        return None
    try:
        from app.writing_invocations import get_invocation

        record = get_invocation(novel_id, invocation_id)
    except Exception:
        record = None
    if not record:
        return None
    analysis = {}
    for event in record.get("events") or []:
        if event.get("event") == "request_analyzed":
            analysis = dict(event.get("details") or {})
    if not analysis:
        return None
    task = analysis.get("task") or record.get("task") or "prose"
    chapter = _clean_int(analysis.get("target_chapter")) or record.get("chapter")
    return {
        "id": invocation_id,
        "novel_id": novel_id,
        "track": _track(record.get("track") or "create"),
        "status": record.get("status") or "unknown",
        "invocation_id": invocation_id,
        "message": record.get("user_message_preview") or "",
        "analysis": analysis,
        "task": task,
        "chapter": chapter,
        "related_files": related_file_context(
            novel_id=novel_id,
            analysis=analysis,
            task=task,
            chapter=chapter,
        ),
        "created_at": record.get("created_at") or "",
        "updated_at": record.get("updated_at") or "",
    }


def _find_record(data: dict[str, Any], novel_id: str, track: str, invocation_id: str | None) -> dict[str, Any] | None:
    items = data.get("items") or {}
    if invocation_id and invocation_id in items:
        return items[invocation_id]
    if invocation_id:
        for item in items.values():
            if item.get("invocation_id") == invocation_id:
                return item
    latest_id = (data.get("latest") or {}).get(_latest_key(novel_id, track))
    if latest_id and latest_id in items:
        return items[latest_id]
    candidates = [
        item for item in items.values()
        if item.get("novel_id") == novel_id and item.get("track") == _track(track)
        and item.get("status") in {"pending", "stale_pending"}
    ]
    candidates.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return candidates[0] if candidates else None


def _find_message_record(data: dict[str, Any], novel_id: str, track: str, message: str) -> dict[str, Any] | None:
    message_hash = _hash(message or "")
    normalized = _normalized_message(message)
    candidates: list[dict[str, Any]] = []
    for item in (data.get("items") or {}).values():
        if item.get("novel_id") != novel_id or item.get("track") != _track(track):
            continue
        if item.get("status") not in {"pending", "stale_pending"}:
            continue
        item_normalized = _normalized_message(item.get("message"))
        if item.get("message_hash") == message_hash or (normalized and item_normalized == normalized):
            candidates.append(item)
    candidates.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return candidates[0] if candidates else None


def _message_match(left: str, right: str) -> tuple[float, str]:
    if _hash(left or "") == _hash(right or ""):
        return 1.0, "hash"
    left_norm = _normalized_message(left)
    right_norm = _normalized_message(right)
    if not left_norm or not right_norm:
        return 0.0, "empty"
    if left_norm == right_norm:
        return 1.0, "normalized"
    return SequenceMatcher(None, left_norm, right_norm).ratio(), "similarity"


def _normalized_message(value: Any) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "").lower())


def _store_long(item: dict[str, Any]) -> None:
    data = _load(LONG_FILE)
    data.setdefault("items", {})[item["id"]] = item
    data.setdefault("latest", {})[_latest_key(item.get("novel_id"), item.get("track"))] = item["id"]
    _write(LONG_FILE, data)


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"items": {}, "latest": {}}
    except Exception:
        return {"items": {}, "latest": {}}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _clean_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    if isinstance(value, str):
        match = re.search(r"第\s*([一二两三四五六七八九十])\s*章", value)
        if match:
            return CN_NUMS.get(match.group(1))
    return None


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text[:120])
    return out[:20]


def _clean_workflow_status(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    stages = [str(item or "").strip() for item in value.get("stages") or []]
    stages = [item for item in stages if item][:40]
    done = [str(item or "").strip() for item in value.get("done") or []]
    done = [item for item in done if item in stages][:40]
    durations_src = value.get("durations_ms") or {}
    durations: dict[str, int] = {}
    if isinstance(durations_src, dict):
        for key, raw in durations_src.items():
            node = str(key or "").strip()
            if node not in stages:
                continue
            try:
                ms = int(float(raw))
            except Exception:
                continue
            if ms >= 0:
                durations[node] = min(ms, 24 * 60 * 60 * 1000)
    current = str(value.get("current") or "").strip()
    if current and current not in stages:
        stages.append(current)
    out = {
        "stages": stages,
        "current": current,
        "done": done,
        "durations_ms": durations,
        "status": str(value.get("status") or "running")[:40],
        "source": str(value.get("source") or "ui")[:40],
        "updated_at": str(value.get("updated_at") or _now())[:40],
        "invocation_id": str(value.get("invocation_id") or "")[:120],
        "task": str(value.get("task") or "")[:80],
        "track": _track(value.get("track") or "create"),
    }
    try:
        chapter = _clean_int(value.get("chapter"))
        if chapter:
            out["chapter"] = chapter
    except Exception:
        pass
    stage_started_at = str(value.get("stage_started_at") or "").strip()
    if stage_started_at:
        out["stage_started_at"] = stage_started_at[:40]
    total_ms = value.get("total_ms")
    try:
        if total_ms is not None:
            out["total_ms"] = min(max(int(float(total_ms)), 0), 24 * 60 * 60 * 1000)
    except Exception:
        pass
    return out


def _track(value: Any) -> str:
    return "create" if str(value or "create") == "create" else "normal"


def _latest_key(novel_id: str | None, track: str | None) -> str:
    return f"{normalize_novel_id(novel_id)}:{_track(track)}"


def _record_id(novel_id: str, track: str, message: str, now: str) -> str:
    return f"pending_{_hash('|'.join([novel_id, _track(track), message, now]))[:16]}"


def _hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
