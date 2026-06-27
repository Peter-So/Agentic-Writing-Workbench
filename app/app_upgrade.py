from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT


REPO = "Peter-So/Agentic-Writing-Workbench"
DATA_FILE = ROOT / "data" / "app-upgrade-status.json"
CHANGE_FILE = ROOT / "CHANGE.md"
TAG_RE = re.compile(r"Agentic-Writing-Workbench-v(?P<version>\d+\.\d+\.\d+)")
ACTIVE_INVOCATION_STATUSES = {"running", "awaiting_confirm", "awaiting_archive"}
ACTIVE_WORKFLOW_STATUSES = {"running", "awaiting_confirm", "awaiting_archive"}

_LOCK = threading.Lock()
_RUNNING_THREAD: threading.Thread | None = None


def current_version() -> str:
    if CHANGE_FILE.exists():
        match = TAG_RE.search(CHANGE_FILE.read_text(encoding="utf-8", errors="replace"))
        if match:
            return f"Agentic-Writing-Workbench-v{match.group('version')}"
    return "unknown"


def check_latest_release() -> dict[str, Any]:
    latest = _latest_release()
    current = current_version()
    latest_tag = str(latest.get("tag_name") or latest.get("name") or "")
    task_report = active_task_report()
    return {
        "ok": True,
        "current_version": current,
        "latest_version": latest_tag,
        "has_update": bool(latest_tag and latest_tag != current),
        "safe_to_upgrade": task_report["safe_to_upgrade"],
        "active_tasks": task_report,
        "release": {
            "tag_name": latest_tag,
            "name": latest.get("name") or latest_tag,
            "body": latest.get("body") or "",
            "html_url": latest.get("html_url") or "",
            "published_at": latest.get("published_at") or "",
        },
    }


def upgrade_status() -> dict[str, Any]:
    data = _read_status()
    data.setdefault("ok", True)
    data.setdefault("status", "idle")
    data.setdefault("stage", "idle")
    data.setdefault("message", "未执行升级")
    data.setdefault("current_version", current_version())
    return data


def active_task_report() -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []
    blockers.extend(_active_provider_jobs())
    blockers.extend(_active_invocations())
    blockers.extend(_active_pending_intents())
    return {
        "ok": True,
        "safe_to_upgrade": not blockers,
        "blockers": blockers,
        "count": len(blockers),
    }


def start_upgrade(host: str = "127.0.0.1", port: int = 7861, version: str = "") -> dict[str, Any]:
    global _RUNNING_THREAD
    with _LOCK:
        if _RUNNING_THREAD and _RUNNING_THREAD.is_alive():
            return {**upgrade_status(), "ok": False, "message": "已有升级任务正在执行"}
        task_report = active_task_report()
        if not task_report["safe_to_upgrade"]:
            return {
                "ok": False,
                "status": "blocked",
                "stage": "active_task_guard",
                "message": "存在未完成任务，请完成、归档或取消后再升级。",
                "active_tasks": task_report,
            }
        release = check_latest_release()
        target_version = version or release.get("latest_version") or ""
        if not target_version:
            raise RuntimeError("没有可用的发布版本")
        _write_status({
            "ok": True,
            "status": "running",
            "stage": "queued",
            "message": "升级任务已排队",
            "current_version": current_version(),
            "target_version": target_version,
            "release": release.get("release") or {},
            "started_at": _now(),
            "updated_at": _now(),
            "backup_dir": "",
            "restart": {"scheduled": False, "host": host, "port": port},
        })
        _RUNNING_THREAD = threading.Thread(
            target=_run_upgrade,
            kwargs={"host": host, "port": port, "version": target_version},
            daemon=True,
        )
        _RUNNING_THREAD.start()
        return upgrade_status()


def _run_upgrade(host: str, port: int, version: str) -> None:
    backup_dir = ""
    rollback_attempted = False
    try:
        _update_status("download", "下载新版内容")
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "upgrade-to-latest.py"),
            "--project-dir",
            str(ROOT),
            "--version",
            version,
        ]
        process = subprocess.Popen(
            cmd,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            _update_status_from_output(line)
        returncode = process.wait()
        output = "".join(lines)
        backup_dir = _extract_backup_dir(output)
        if returncode != 0:
            rollback_attempted = "已自动回滚" in output or "回滚完成" in output
            _write_status({
                **upgrade_status(),
                "ok": False,
                "status": "failed",
                "stage": "rollback" if rollback_attempted else "failed",
                "message": "升级失败，已回滚" if rollback_attempted else "升级失败，未生成可回滚备份",
                "backup_dir": backup_dir,
                "log": output[-8000:],
                "rollback_attempted": rollback_attempted,
                "updated_at": _now(),
            })
            if rollback_attempted:
                _schedule_restart(host, port)
            return
        _write_status({
            **upgrade_status(),
            "ok": True,
            "status": "restarting",
            "stage": "restart",
            "message": "升级完成，正在重启服务",
            "backup_dir": backup_dir,
            "log": output[-8000:],
            "updated_at": _now(),
        })
        _schedule_restart(host, port)
    except Exception as exc:
        _write_status({
            **upgrade_status(),
            "ok": False,
            "status": "failed",
            "stage": "failed",
            "message": f"升级失败：{type(exc).__name__}: {exc}",
            "backup_dir": backup_dir,
            "rollback_attempted": rollback_attempted,
            "updated_at": _now(),
        })


def _latest_release() -> dict[str, Any]:
    url = f"https://api.github.com/repos/{REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise RuntimeError(f"检查 GitHub Release 失败：{exc}") from exc


def _active_provider_jobs() -> list[dict[str, Any]]:
    try:
        from app.ai_provider_jobs import jobs

        return [
            {
                "type": "provider_job",
                "id": item.get("job_id", ""),
                "status": "running" if any(
                    provider.get("status") == "running" for provider in item.get("providers") or []
                ) else "queued",
                "message": "网页 provider 协同任务未完成",
                "providers": [
                    {
                        "provider": provider.get("provider", ""),
                        "status": provider.get("status", ""),
                    }
                    for provider in item.get("providers") or []
                    if provider.get("status") in {"queued", "running"}
                ],
            }
            for item in jobs.active_jobs()
        ]
    except Exception:
        return []


def _active_invocations() -> list[dict[str, Any]]:
    try:
        from app.novel_context import list_novels
        from app.writing_invocations import list_recent_invocations
    except Exception:
        return []
    blockers: list[dict[str, Any]] = []
    for novel in list_novels():
        novel_id = str(novel.get("id") if isinstance(novel, dict) else novel)
        if not novel_id:
            continue
        for record in list_recent_invocations(novel_id, limit=8):
            status = str(record.get("status") or "")
            if status not in ACTIVE_INVOCATION_STATUSES:
                continue
            blockers.append({
                "type": "invocation",
                "id": record.get("id", ""),
                "novel_id": novel_id,
                "status": status,
                "task": record.get("task", ""),
                "message": "创作任务未完成",
                "updated_at": record.get("updated_at", ""),
            })
    return blockers


def _active_pending_intents() -> list[dict[str, Any]]:
    try:
        from app.pending_intent_memory import SHORT_FILE

        data = json.loads(SHORT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    blockers: list[dict[str, Any]] = []
    for item in (data.get("items") or {}).values():
        status = str(item.get("status") or "")
        if status not in {"pending", "stale_pending"}:
            continue
        workflow = item.get("workflow_status") if isinstance(item.get("workflow_status"), dict) else {}
        workflow_status = str(workflow.get("status") or status)
        if workflow_status and workflow_status not in ACTIVE_WORKFLOW_STATUSES and status != "pending":
            continue
        blockers.append({
            "type": "pending_intent",
            "id": item.get("id", ""),
            "invocation_id": item.get("invocation_id", ""),
            "novel_id": item.get("novel_id", ""),
            "status": workflow_status or status,
            "task": item.get("task", ""),
            "message": "存在可恢复的未完成任务",
            "updated_at": item.get("updated_at", ""),
        })
    return blockers


def _schedule_restart(host: str, port: int) -> None:
    _write_status({
        **upgrade_status(),
        "restart": {"scheduled": True, "host": host, "port": port, "scheduled_at": _now()},
        "updated_at": _now(),
    })
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "restart-workbench.py"),
        "--project-dir",
        str(ROOT),
        "--host",
        host,
        "--port",
        str(port),
        "--parent-pid",
        str(os.getpid()),
    ]
    kwargs: dict[str, Any] = {
        "cwd": ROOT,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
    threading.Thread(target=_exit_soon, daemon=True).start()


def _exit_soon() -> None:
    time.sleep(1.2)
    os._exit(0)


def _read_status() -> dict[str, Any]:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_status(data: dict[str, Any]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def _update_status(stage: str, message: str) -> None:
    _write_status({
        **upgrade_status(),
        "status": "running",
        "stage": stage,
        "message": message,
        "updated_at": _now(),
    })


def _update_status_from_output(line: str) -> None:
    text = line.strip()
    if not text:
        return
    if "下载发布包" in text:
        _update_status("download", "下载新版内容")
    elif "开始升级" in text:
        _update_status("backup", "创建备份并更新框架文件")
    elif "回滚完成" in text or "自动回滚" in text:
        _update_status("rollback", "升级失败，正在回滚")
    elif "升级完成" in text:
        _update_status("apply", "框架文件更新完成")


def _extract_backup_dir(text: str) -> str:
    match = re.search(r"备份目录：(.+)", text or "")
    if not match:
        return ""
    value = match.group(1).strip().strip('"')
    try:
        return str(Path(value).resolve())
    except Exception:
        return value


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
