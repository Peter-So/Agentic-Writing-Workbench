from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import normalize_novel_id
from app.project_paths import outputs_dir
from app.project_kinds import project_kind
from app.writing_invocations import get_invocation
from app.writing_sop import sop_for_task


def review_packet(novel_id: str, invocation_id: str, write_file: bool = True) -> dict[str, Any] | None:
    nid = normalize_novel_id(novel_id)
    record = get_invocation(nid, invocation_id)
    if record is None:
        return None
    kind = project_kind(nid)
    task = record.get("task", "")
    sop = record.get("workflow_sop") or sop_for_task(kind, task)
    packet = _structured_packet(nid, kind, record, sop)
    markdown = _packet_markdown(packet)
    path = ""
    if write_file:
        out_dir = outputs_dir(nid)
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"review_packet_{invocation_id}.md"
        target.write_text(markdown, encoding="utf-8")
        path = _rel(target)
    return {"ok": True, "packet": packet, "markdown": markdown, "path": path}


def _structured_packet(
    novel_id: str,
    kind: str,
    record: dict[str, Any],
    sop: dict[str, Any],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "novel_id": novel_id,
        "project_kind": kind,
        "invocation": {
            "id": record.get("id", ""),
            "status": record.get("status", ""),
            "task": record.get("task", ""),
            "mode": record.get("mode", ""),
            "chapter": record.get("chapter"),
            "created_at": record.get("created_at", ""),
            "updated_at": record.get("updated_at", ""),
            "user_message_hash": record.get("user_message_hash", ""),
            "user_message_preview": record.get("user_message_preview", ""),
        },
        "sop": {
            "stage": sop.get("stage", ""),
            "role": sop.get("role_label") or sop.get("role_id") or "",
            "mode": sop.get("mode", ""),
            "hard_rules": [_rule_text(item) for item in sop.get("hard_rules") or []],
            "review_focus": sop.get("review_focus") or [],
        },
        "artifacts": record.get("artifacts") or {},
        "acceptance_checklist": _acceptance_checklist(sop),
        "timeline_counts": {
            "events": len(record.get("events") or []),
            "trajectory": len(record.get("trajectory") or []),
        },
    }


def _packet_markdown(packet: dict[str, Any]) -> str:
    inv = packet["invocation"]
    lines = [
        f"# Review Packet - {inv.get('id')}",
        "",
        f"- 项目：{packet.get('novel_id')} / {packet.get('project_kind')}",
        f"- 状态：{inv.get('status')}，任务：{inv.get('task')}，模式：{inv.get('mode')}",
        f"- 用户需求 hash：{inv.get('user_message_hash')}",
        f"- 用户需求预览：{inv.get('user_message_preview')}",
        "",
        "## SOP 与验收",
        f"- 阶段：{packet['sop'].get('stage')}",
        f"- 角色：{packet['sop'].get('role')}",
        f"- 协作模式：{packet['sop'].get('mode')}",
    ]
    lines.extend(f"- 硬规则：{item}" for item in packet["sop"].get("hard_rules") or ["无"])
    lines.append("")
    lines.append("## 验收清单")
    lines.extend(f"- [ ] {item}" for item in packet.get("acceptance_checklist") or [])
    return "\n".join(lines).strip() + "\n"


def _acceptance_checklist(sop: dict[str, Any]) -> list[str]:
    checklist = [_rule_text(item) for item in sop.get("hard_rules") or []]
    checklist.extend(str(item) for item in sop.get("review_focus") or [])
    seen: set[str] = set()
    result: list[str] = []
    for item in checklist:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result




def _rule_text(rule: Any) -> str:
    if isinstance(rule, dict):
        return str(rule.get("text") or rule.get("id") or "")
    return str(rule)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
