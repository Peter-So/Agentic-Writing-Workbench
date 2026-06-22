from __future__ import annotations

from typing import Any

from app.chat_history import load_by_track


def intervene_stats(track: str = "create") -> dict[str, Any]:
    """聚合人工干预记录，按 task 算 confirm/reject/改写率、样本数，供学习优化创作流程。"""
    items = load_by_track(track).get("messages", [])
    by_task: dict[str, dict[str, int]] = {}
    for it in items:
        if it.get("kind") != "intervene":
            continue
        data = it.get("data") or {}
        task = data.get("task") or "unknown"
        dec = data.get("decision") or "other"
        slot = by_task.setdefault(task, {"confirm": 0, "reject": 0, "other": 0, "total": 0})
        if dec in slot:
            slot[dec] += 1
        slot["total"] += 1

    def _rates(slot: dict[str, int]) -> dict[str, Any]:
        t = slot["total"] or 1
        return {
            **slot,
            "confirm_rate": round(slot["confirm"] / t, 3),
            "reject_rate": round(slot["reject"] / t, 3),
            "rewrite_rate": round(slot["other"] / t, 3),
        }

    return {"track": track, "by_task": {k: _rates(v) for k, v in by_task.items()}}


def loop_stats(track: str = "create") -> dict[str, Any]:
    """从对话记录的 assistant 回合 meta（actions）估算各 task 平均审查回环次数。

    actions 里 generate/merge 出现次数 ≈ 该回合生成/融合轮数。
    """
    items = load_by_track(track).get("messages", [])
    by_task: dict[str, dict[str, Any]] = {}
    for it in items:
        if it.get("role") != "assistant":
            continue
        meta = it.get("meta") or ""
        data = it.get("data") or {}
        task = (data.get("task") if isinstance(data, dict) else None) or "unknown"
        # meta 形如 "assemble_material -> generate(claude) -> ..."；数 generate/merge 次数
        loops = meta.count("generate(") + meta.count("merge(")
        if loops <= 0:
            continue
        slot = by_task.setdefault(task, {"samples": 0, "loops_total": 0})
        slot["samples"] += 1
        slot["loops_total"] += loops
    out = {}
    for k, v in by_task.items():
        n = v["samples"] or 1
        out[k] = {"samples": v["samples"], "avg_loops": round(v["loops_total"] / n, 2)}
    return {"track": track, "by_task": out}


def all_stats(track: str = "create") -> dict[str, Any]:
    return {"intervene": intervene_stats(track), "loops": loop_stats(track)}
