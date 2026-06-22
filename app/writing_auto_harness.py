from __future__ import annotations

from typing import Any

from app.novel_context import normalize_novel_id
from app.writing_invocations import list_recent_invocations


def harness_suggestions(novel_id: str, limit: int = 20) -> dict[str, Any]:
    """Suggest harness/SOP guard candidates from recent invocation failures.

    This module deliberately does not mutate SOP files. It is an Auto Harness
    assistant: collect evidence, propose a candidate, leave adoption to humans.
    """
    nid = normalize_novel_id(novel_id)
    records = list_recent_invocations(nid, limit=limit)
    suggestions: dict[str, dict[str, Any]] = {}

    for record in records:
        inv_id = record.get("id", "")
        status = record.get("status", "")
        task = record.get("task", "")
        _collect_harness_issues(suggestions, inv_id, task, record.get("harness") or [])
        _collect_budget_issues(suggestions, inv_id, task, record.get("budgets") or [])
        _collect_route_issues(suggestions, inv_id, task, record.get("routes") or [])
        if status == "failed":
            _add(
                suggestions,
                "auto-failed-invocation-review-packet",
                "warn",
                "review_packet",
                "最近有失败 invocation，建议生成 Review Packet 复盘失败节点、provider 状态和门禁结果。",
                {"action": "open_review_packet"},
                inv_id,
                "failed_invocation",
                {"task": task},
            )

    return {
        "ok": True,
        "novel_id": nid,
        "limit": limit,
        "suggestions": sorted(
            suggestions.values(),
            key=lambda item: _severity_rank(item.get("severity", "info")),
            reverse=True,
        ),
    }


def _collect_harness_issues(
    suggestions: dict[str, dict[str, Any]],
    invocation_id: str,
    task: str,
    harness_items: list[Any],
) -> None:
    for item in harness_items:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        for issue in result.get("issues") or []:
            code = issue.get("code") or "harness_issue"
            if code == "short_film_concept_word_limit_conflict":
                _add(
                    suggestions,
                    "auto-short-film-concept-output-width",
                    "blocker",
                    "sop_predicate",
                    "短片“概念”任务曾被压成一句话/50字 logline，建议保留禁止窄化输出的 SOP predicate。",
                    {
                        "type": "prompt_pattern_absent",
                        "scope": "output_requirements",
                        "forbidden": ["一句话故事", "不超过\\s*50\\s*字", "只输出.*logline"],
                    },
                    invocation_id,
                    code,
                    {"task": task},
                )
            elif code == "screenplay_format_missing":
                _add(
                    suggestions,
                    "auto-screenplay-format-required",
                    "blocker",
                    "sop_predicate",
                    "剧本任务缺少影视脚本字段时应被拦截，建议把场景标题/动作/角色/对白设为强约束。",
                    {
                        "type": "prompt_contains_all",
                        "required": ["场景标题", "动作", "角色", "对白"],
                    },
                    invocation_id,
                    code,
                    {"task": task},
                )
            elif code == "sop_rules_not_embedded":
                _add(
                    suggestions,
                    "auto-sop-rules-embedded",
                    "warn",
                    "prompt_assembler",
                    "provider 提问包未完整嵌入 SOP 硬规则，建议检查材料组装模板和 format_sop_for_prompt。",
                    {"check": "embed_hard_rules"},
                    invocation_id,
                    code,
                    {"task": task, "details": issue.get("details") or {}},
                )
            elif issue.get("level") in {"warn", "error"}:
                _add(
                    suggestions,
                    f"auto-harness-{code}",
                    "warn" if issue.get("level") == "warn" else "blocker",
                    "harness_rule",
                    issue.get("message") or f"Harness issue: {code}",
                    {"code": code},
                    invocation_id,
                    code,
                    {"task": task, "details": issue.get("details") or {}},
                )


def _collect_budget_issues(
    suggestions: dict[str, dict[str, Any]],
    invocation_id: str,
    task: str,
    budgets: list[Any],
) -> None:
    for item in budgets:
        if not isinstance(item, dict):
            continue
        level = item.get("level")
        if level in {"warn", "error"}:
            _add(
                suggestions,
                "auto-budget-fanout-cap",
                "warn" if level == "warn" else "blocker",
                "route_policy",
                "fanout 预算进入高风险区，建议先裁剪材料或改走单 Agent 串行转化。",
                {
                    "prompt_tokens_est": item.get("prompt_tokens_est", 0),
                    "fanout_prompt_tokens_est": item.get("fanout_prompt_tokens_est", 0),
                },
                invocation_id,
                f"budget_{level}",
                {"task": task},
            )


def _collect_route_issues(
    suggestions: dict[str, dict[str, Any]],
    invocation_id: str,
    task: str,
    routes: list[Any],
) -> None:
    for item in routes:
        if not isinstance(item, dict):
            continue
        reason = item.get("reason")
        if reason == "fanout_budget_too_high":
            _add(
                suggestions,
                "auto-route-budget-single-agent",
                "blocker",
                "route_policy",
                "路由因预算过高改走单 Agent，建议在 UI 上提示用户先选择材料范围再 fanout。",
                {"decision": item.get("decision"), "reason": reason},
                invocation_id,
                reason,
                {"task": task},
            )


def _add(
    suggestions: dict[str, dict[str, Any]],
    sid: str,
    severity: str,
    target: str,
    reason: str,
    candidate: dict[str, Any],
    invocation_id: str,
    code: str,
    details: dict[str, Any] | None = None,
) -> None:
    item = suggestions.setdefault(
        sid,
        {
            "id": sid,
            "severity": severity,
            "target": target,
            "reason": reason,
            "candidate": candidate,
            "evidence": [],
        },
    )
    item["evidence"].append({
        "invocation_id": invocation_id,
        "code": code,
        "details": details or {},
    })


def _severity_rank(severity: str) -> int:
    return {"blocker": 3, "warn": 2, "info": 1}.get(severity, 0)
