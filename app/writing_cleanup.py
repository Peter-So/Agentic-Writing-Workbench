from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import WRITING_ROOT, novel_dir, normalize_novel_id
from app.project_paths import storyboards_dir


TMP_TTL_HOURS = float(os.getenv("WRITING_CLEANUP_TMP_TTL_HOURS", "24"))
DEBUG_TTL_HOURS = float(os.getenv("WRITING_CLEANUP_DEBUG_TTL_HOURS", "72"))
KEEP_DEBUG_FILES = int(os.getenv("WRITING_CLEANUP_KEEP_DEBUG", "20"))
KEEP_SUPERSEDED_DIRS = int(os.getenv("WRITING_CLEANUP_KEEP_SUPERSEDED", "3"))

RECOVERY_PARTS = {
    "记忆",
    "memory",
    "日志",
    "logs",
    "调用记录",
    "invocations",
    "维基",
    "wiki",
}


def cleanup_after_task(
    novel_id: str | None,
    *,
    task_scope: str = "task",
    dry_run: bool = False,
    include_global: bool = False,
) -> dict[str, Any]:
    """Clean task-local scratch files without touching recovery/audit data.

    This is intentionally whitelist-based. Invocation logs, pending intent
    memory, project/LLM Wiki, RAG corpora, material bundles, provider request
    packets and formal outputs are durable workflow artifacts, so they are not
    removed here even if they look like process files.
    """
    nid = normalize_novel_id(novel_id)
    summary = _new_summary(nid, dry_run=dry_run, task_scope=task_scope)
    project_root = novel_dir(nid)
    if project_root.exists():
        _collect_stale_tmp_files(project_root, summary, dry_run=dry_run)
        _collect_storyboard_debug(nid, summary, dry_run=dry_run)
        _collect_storyboard_queue(nid, summary, dry_run=dry_run)
        _collect_superseded_storyboard_backups(nid, summary, dry_run=dry_run)

    if include_global:
        _collect_pycache(WRITING_ROOT, summary, dry_run=dry_run)
        _collect_pycache(ROOT / "app", summary, dry_run=dry_run)
        _collect_pycache(ROOT / "scripts", summary, dry_run=dry_run)
        _collect_stale_tmp_files(ROOT / "app", summary, dry_run=dry_run)
        _collect_stale_tmp_files(ROOT / "data", summary, dry_run=dry_run)

    summary["removed_count"] = len(summary["removed"])
    summary["kept_count"] = len(summary["kept"])
    summary["error_count"] = len(summary["errors"])
    summary["ok"] = not summary["errors"]
    return summary


def cleanup_preview(novel_id: str | None, *, include_global: bool = False) -> dict[str, Any]:
    return cleanup_after_task(novel_id, dry_run=True, include_global=include_global, task_scope="preview")


def cleanup_health(novel_id: str | None) -> dict[str, Any]:
    preview = cleanup_preview(novel_id)
    candidates = preview.get("removed") or []
    bytes_total = sum(int(item.get("bytes") or 0) for item in candidates)
    level = "warn" if candidates else "ok"
    message = "未发现任务临时缓存残留。"
    if candidates:
        message = f"发现 {len(candidates)} 项可清理残留，预计释放 {_format_bytes(bytes_total)}。"
    return {
        "level": level,
        "message": message,
        "preview": {
            "candidate_count": len(candidates),
            "bytes": bytes_total,
            "sample": candidates[:8],
        },
    }


def _new_summary(novel_id: str, *, dry_run: bool, task_scope: str) -> dict[str, Any]:
    return {
        "ok": True,
        "novel_id": novel_id,
        "dry_run": dry_run,
        "task_scope": task_scope,
        "removed": [],
        "kept": [],
        "errors": [],
        "freed_bytes": 0,
    }


def _collect_stale_tmp_files(root: Path, summary: dict[str, Any], *, dry_run: bool) -> None:
    if not root.exists():
        return
    cutoff = time.time() - TMP_TTL_HOURS * 3600
    for path in root.rglob("*.tmp"):
        if not path.is_file():
            continue
        if _is_recovery_path(path):
            _keep(summary, path, "恢复/审计目录中的原子写临时文件，不自动清理")
            continue
        try:
            if path.stat().st_mtime > cutoff:
                _keep(summary, path, f"{TMP_TTL_HOURS:g} 小时内的临时文件，可能仍被写入流程使用")
                continue
        except OSError as exc:
            _error(summary, path, exc)
            continue
        _remove(summary, path, "过期原子写 .tmp 残留", dry_run=dry_run)


def _collect_storyboard_debug(novel_id: str, summary: dict[str, Any], *, dry_run: bool) -> None:
    debug_dir = storyboards_dir(novel_id, "_api_debug")
    if not debug_dir.is_dir():
        return
    files = sorted((path for path in debug_dir.glob("*.json") if path.is_file()), key=_mtime, reverse=True)
    cutoff = time.time() - DEBUG_TTL_HOURS * 3600
    for idx, path in enumerate(files):
        if idx < KEEP_DEBUG_FILES:
            _keep(summary, path, f"保留最近 {KEEP_DEBUG_FILES} 个生图 API 调试响应")
            continue
        if _mtime(path) > cutoff:
            _keep(summary, path, f"{DEBUG_TTL_HOURS:g} 小时内的生图调试响应")
            continue
        _remove(summary, path, "过期生图 API 调试响应", dry_run=dry_run)
    _remove_empty_dir(summary, debug_dir, "空的生图 API 调试目录", dry_run=dry_run)


def _collect_storyboard_queue(novel_id: str, summary: dict[str, Any], *, dry_run: bool) -> None:
    queue_path = storyboards_dir(novel_id, "image_generation_queue.json")
    if not queue_path.is_file():
        return
    data = _read_json(queue_path)
    status = str(data.get("status") or "").strip()
    if status == "done":
        _remove(summary, queue_path, "已完成的分镜生图队列，可由 manifest 和图片目录复盘", dry_run=dry_run)
    elif status == "partial_failed":
        _keep(summary, queue_path, "分镜生图部分失败，保留队列用于恢复失败项")
    else:
        _keep(summary, queue_path, f"分镜生图队列状态为 {status or '未知'}，保留用于继续执行")


def _collect_superseded_storyboard_backups(novel_id: str, summary: dict[str, Any], *, dry_run: bool) -> None:
    root = storyboards_dir(novel_id)
    if not root.is_dir():
        return
    for sup_dir in root.rglob("_superseded"):
        if not sup_dir.is_dir():
            continue
        backups = sorted((path for path in sup_dir.iterdir() if path.is_dir()), key=_mtime, reverse=True)
        for idx, path in enumerate(backups):
            if idx < KEEP_SUPERSEDED_DIRS:
                _keep(summary, path, f"保留最近 {KEEP_SUPERSEDED_DIRS} 份帧图替换备份，用于重生参考")
                continue
            _remove(summary, path, "过旧帧图替换备份", dry_run=dry_run)
        _remove_empty_dir(summary, sup_dir, "空的帧图替换备份目录", dry_run=dry_run)


def _collect_pycache(root: Path, summary: dict[str, Any], *, dry_run: bool) -> None:
    if not root.exists():
        return
    for path in root.rglob("__pycache__"):
        if path.is_dir():
            _remove(summary, path, "Python 字节码缓存（手动维护清理）", dry_run=dry_run)


def _remove(summary: dict[str, Any], path: Path, reason: str, *, dry_run: bool) -> None:
    if not _is_safe_cleanup_target(path):
        _keep(summary, path, "路径不在允许清理范围内")
        return
    size = _size(path)
    item = {"path": _rel(path), "bytes": size, "reason": reason}
    if dry_run:
        summary["removed"].append(item)
        summary["freed_bytes"] += size
        return
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        summary["removed"].append(item)
        summary["freed_bytes"] += size
    except OSError as exc:
        _error(summary, path, exc)


def _remove_empty_dir(summary: dict[str, Any], path: Path, reason: str, *, dry_run: bool) -> None:
    try:
        if path.is_dir() and not any(path.iterdir()):
            _remove(summary, path, reason, dry_run=dry_run)
    except OSError as exc:
        _error(summary, path, exc)


def _keep(summary: dict[str, Any], path: Path, reason: str) -> None:
    summary["kept"].append({"path": _rel(path), "bytes": _size(path), "reason": reason})


def _error(summary: dict[str, Any], path: Path, exc: BaseException) -> None:
    summary["errors"].append({"path": _rel(path), "error": f"{type(exc).__name__}: {exc}"})


def _is_recovery_path(path: Path) -> bool:
    try:
        parts = set(path.resolve().relative_to(WRITING_ROOT.resolve()).parts)
    except ValueError:
        return False
    return bool(parts & RECOVERY_PARTS)


def _is_safe_cleanup_target(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    allowed_roots = [WRITING_ROOT.resolve(), (ROOT / "app").resolve(), (ROOT / "data").resolve()]
    return any(resolved == root or root in resolved.parents for root in allowed_roots)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for child in path.rglob("*"):
            if child.is_file():
                total += child.stat().st_size
        return total
    except OSError:
        return 0


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{size}B"
