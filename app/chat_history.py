from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import DEFAULT_NOVEL_ID, normalize_novel_id

# 对话记录持久化：最新 100 条留在主文件，超出的滚入历史文件。
# CHAT_HISTORY_DIR 环境变量可覆盖目录（测试用，避免误写/误删真实记录）。
HISTORY_DIR = Path(os.getenv("CHAT_HISTORY_DIR") or (ROOT / "data" / "chat_history"))
RECENT_FILE = HISTORY_DIR / "recent.jsonl"
ARCHIVE_FILE = HISTORY_DIR / "archive.jsonl"
RECENT_LIMIT = 100
PAGE_SIZE = 20

_lock = threading.Lock()


def _read_lines(path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    return items


def _write_lines(path, items: list[dict[str, Any]]) -> None:
    """原子写：先写同目录 .tmp 再 os.replace 覆盖，避免写入中途崩溃导致文件半截损坏。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def append_message(message: dict[str, Any]) -> dict[str, Any]:
    """追加一条消息；维持 recent 最多 100 条，溢出的最旧记录滚入 archive。

    每条记录带 track 字段（create=创作模式 / normal=非创作模式），
    展示仍是统一时间线（顺序不变），track 仅用于后续按模式学习/优化创作流程。
    """
    with _lock:
        recent = _read_lines(RECENT_FILE)
        seq = 1
        if recent:
            seq = int(recent[-1].get("seq", len(recent))) + 1
        else:
            archive = _read_lines(ARCHIVE_FILE)
            if archive:
                seq = int(archive[-1].get("seq", len(archive))) + 1
        message = {
            **message,
            "seq": seq,
            "track": _norm_track(message.get("track")),
            "novel_id": _norm_novel_id(message.get("novel_id")),
        }
        recent.append(message)
        if len(recent) > RECENT_LIMIT:
            overflow = recent[:-RECENT_LIMIT]
            recent = recent[-RECENT_LIMIT:]
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            with ARCHIVE_FILE.open("a", encoding="utf-8") as fh:
                for item in overflow:
                    fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        _write_lines(RECENT_FILE, recent)
        return message


def _norm_track(track: Any) -> str:
    """归一化 track；缺省或非法值视为 normal（旧记录无此字段时即普通轨道）。"""
    return "create" if track == "create" else "normal"


def _norm_novel_id(novel_id: Any) -> str:
    """归一化项目编号；旧记录无 novel_id 时归入 001，保持兼容。"""
    try:
        return normalize_novel_id(str(novel_id or DEFAULT_NOVEL_ID))
    except Exception:
        return DEFAULT_NOVEL_ID


def _filter_project(items: list[dict[str, Any]], novel_id: str | None) -> list[dict[str, Any]]:
    nid = _norm_novel_id(novel_id)
    return [item for item in items if _norm_novel_id(item.get("novel_id")) == nid]


def load_recent(novel_id: str | None = None) -> dict[str, Any]:
    """加载当前项目最新最多 100 条（按时间正序）；返回最旧 seq 以便向上加载历史。"""
    with _lock:
        combined = _read_lines(ARCHIVE_FILE) + _read_lines(RECENT_FILE)
    messages = _filter_project(combined, novel_id)[-RECENT_LIMIT:]
    oldest_seq = messages[0]["seq"] if messages else None
    return {"messages": messages, "oldest_seq": oldest_seq, "novel_id": _norm_novel_id(novel_id)}


def load_before(seq: int, limit: int = PAGE_SIZE, novel_id: str | None = None) -> dict[str, Any]:
    """向上加载当前项目 seq 之前的历史记录（archive + recent 合并视图），每次最多 20 条。"""
    with _lock:
        combined = _read_lines(ARCHIVE_FILE) + _read_lines(RECENT_FILE)
    older = [item for item in _filter_project(combined, novel_id) if int(item.get("seq", 0)) < seq]
    page = older[-limit:] if limit else older
    oldest_seq = page[0]["seq"] if page else None
    return {"messages": page, "oldest_seq": oldest_seq, "has_more": len(older) > len(page),
            "novel_id": _norm_novel_id(novel_id)}


def clear_all() -> None:
    with _lock:
        for path in (RECENT_FILE, ARCHIVE_FILE):
            if path.exists():
                path.unlink()


def soft_delete_project_messages(novel_id: str | None, trash_dir: Path | None = None) -> dict[str, Any]:
    """Move one project's chat records out of active history without destroying them."""
    nid = _norm_novel_id(novel_id)
    trash_root = trash_dir or (HISTORY_DIR / ".trash")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    removed: list[dict[str, Any]] = []
    with _lock:
        recent = _read_lines(RECENT_FILE)
        archive = _read_lines(ARCHIVE_FILE)
        kept_recent = []
        kept_archive = []
        for item in recent:
            if _norm_novel_id(item.get("novel_id")) == nid:
                removed.append(item)
            else:
                kept_recent.append(item)
        for item in archive:
            if _norm_novel_id(item.get("novel_id")) == nid:
                removed.append(item)
            else:
                kept_archive.append(item)
        if removed:
            trash_root.mkdir(parents=True, exist_ok=True)
            target = trash_root / f"{nid}_{stamp}.jsonl"
            with target.open("w", encoding="utf-8") as fh:
                for item in removed:
                    fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            _write_lines(RECENT_FILE, kept_recent)
            _write_lines(ARCHIVE_FILE, kept_archive)
            return {"ok": True, "count": len(removed), "trash_path": str(target)}
    return {"ok": True, "count": 0, "trash_path": ""}


def load_by_track(track: str, limit: int = 0, novel_id: str | None = None) -> dict[str, Any]:
    """按 track（create/normal）加载该模式的全部对话记录（archive+recent 合并视图）。

    供后续"创作模式对话记忆/学习优化创作流程"使用；不影响主时间线展示。
    limit=0 表示全部，否则取最新 limit 条。
    """
    track = _norm_track(track)
    with _lock:
        combined = _read_lines(ARCHIVE_FILE) + _read_lines(RECENT_FILE)
    items = [it for it in combined if _norm_track(it.get("track")) == track]
    if novel_id:
        items = _filter_project(items, novel_id)
    if limit:
        items = items[-limit:]
    return {"track": track, "messages": items, "count": len(items), "novel_id": _norm_novel_id(novel_id)}
