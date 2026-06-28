from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.novel_context import WRITING_ROOT
from app.project_kinds import DEFAULT_KIND, SHORT_FILM_KIND, STRONG_NOVEL_KIND


SOP_ROOT = WRITING_ROOT / "sop-definitions"

_SOP_FILE_BY_KIND = {
    STRONG_NOVEL_KIND: "novel_strong.yaml",
    SHORT_FILM_KIND: "short_film.yaml",
    DEFAULT_KIND: "generic.yaml",
}


def sop_for_task(project_kind: str | None, task: str | None) -> dict[str, Any]:
    """Return the workflow SOP slice for a project kind + task.

    This is an informational contract in P1: prompts, UI and invocation logs read
    from the same source, while hard blocking remains in existing review nodes.
    """
    kind = project_kind or DEFAULT_KIND
    sop = load_sop(kind)
    tasks = sop.get("tasks") or {}
    task_key = task or "draft"
    if kind == STRONG_NOVEL_KIND:
        try:
            from app.writing_task_profiles import normalize_novel_task

            task_key = normalize_novel_task(task_key) or task_key
        except Exception:
            pass
    task_sop = dict(tasks.get(task_key) or tasks.get("draft") or tasks.get("outline") or {})
    role_key = task_sop.get("role") or ""
    role = dict((sop.get("roles") or {}).get(role_key) or {})
    return {
        "id": sop.get("id", kind),
        "project_kind": sop.get("project_kind", kind),
        "label": sop.get("label", kind),
        "task": task_key,
        "stage": task_sop.get("stage") or task_key,
        "role_id": role_key,
        "role_label": role.get("label") or role_key or "创作助手",
        "role_duty": role.get("duty") or "",
        "mode": task_sop.get("mode") or sop.get("default_mode") or "parallel_collect_then_serial_merge",
        "confirmation_gate": task_sop.get("confirmation_gate") or "material_selection",
        "hard_rules": list(task_sop.get("hard_rules") or []),
        "review_focus": list(task_sop.get("review_focus") or []),
    }


def format_sop_for_prompt(sop: dict[str, Any]) -> str:
    rules = "\n".join(f"- {_rule_text(item)}" for item in sop.get("hard_rules") or []) or "- 按当前任务规范执行。"
    focus = "\n".join(f"- {item}" for item in sop.get("review_focus") or []) or "- 检查是否覆盖用户要求。"
    mode_label = {
        "parallel_collect_then_serial_merge": "多 provider 并行征集材料，用户确认后串行融合成稿",
        "serial_transform": "基于已确认材料串行转化为下游产物",
        "serial_repair": "针对明确问题串行修复",
    }.get(sop.get("mode"), sop.get("mode") or "未指定")
    gate_label = {
        "material_selection": "provider 材料确认门",
        "final_acceptance": "最终采纳确认门",
    }.get(sop.get("confirmation_gate"), sop.get("confirmation_gate") or "确认门")
    return "\n".join([
        f"- SOP：{sop.get('label')} / {sop.get('stage')}",
        f"- 当前创作角色：{sop.get('role_label')}。{sop.get('role_duty')}",
        f"- 协作模式：{mode_label}",
        f"- 确认门：{gate_label}",
        "- 本阶段硬规则：",
        rules,
        "- 后续审查重点：",
        focus,
    ])


def sop_summary(project_kind: str | None) -> dict[str, Any]:
    sop = load_sop(project_kind or DEFAULT_KIND)
    roles = sop.get("roles") or {}
    tasks = sop.get("tasks") or {}
    return {
        "id": sop.get("id"),
        "project_kind": sop.get("project_kind"),
        "label": sop.get("label"),
        "default_mode": sop.get("default_mode"),
        "roles": roles,
        "tasks": {
            task: {
                "stage": item.get("stage"),
                "role": item.get("role"),
                "role_label": (roles.get(item.get("role")) or {}).get("label"),
                "mode": item.get("mode") or sop.get("default_mode"),
                "confirmation_gate": item.get("confirmation_gate"),
                "hard_rules": item.get("hard_rules") or [],
                "review_focus": item.get("review_focus") or [],
                "predicate_count": sum(1 for rule in (item.get("hard_rules") or []) if isinstance(rule, dict) and rule.get("predicate")),
            }
            for task, item in tasks.items()
        },
    }


@lru_cache(maxsize=8)
def load_sop(project_kind: str) -> dict[str, Any]:
    kind = project_kind if project_kind in _SOP_FILE_BY_KIND else DEFAULT_KIND
    path = SOP_ROOT / _SOP_FILE_BY_KIND[kind]
    if not path.is_file():
        return _fallback_sop(kind)
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or _fallback_sop(kind)
    except Exception:
        return _fallback_sop(kind)


def _fallback_sop(kind: str) -> dict[str, Any]:
    return {
        "id": kind,
        "project_kind": kind,
        "label": "通用创作 SOP",
        "default_mode": "parallel_collect_then_serial_merge",
        "roles": {
            "drafter": {"label": "创作助手", "duty": "负责基于材料输出可编辑内容。"},
        },
        "tasks": {
            "draft": {
                "stage": "草稿生成",
                "role": "drafter",
                "mode": "parallel_collect_then_serial_merge",
                "confirmation_gate": "material_selection",
                "hard_rules": ["必须基于用户要求和项目材料输出。"],
                "review_focus": ["是否覆盖用户目标。"],
            },
        },
    }


def _rule_text(rule: Any) -> str:
    if isinstance(rule, dict):
        return str(rule.get("text") or rule.get("id") or "")
    return str(rule)
