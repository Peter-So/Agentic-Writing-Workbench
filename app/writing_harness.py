from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from app.project_kinds import SHORT_FILM_KIND


PROMPT_WARN_TOKENS = 45000
PROMPT_ERROR_TOKENS = 90000
FANOUT_WARN_TOKENS = 120000
FANOUT_ERROR_TOKENS = 240000

FANOUT_TASKS = {"character", "outline", "beat_sheet", "prose", "logline", "screenplay"}
SINGLE_AGENT_TASKS = {"fix", "shot_list"}


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def estimate_text_tokens(text: str) -> int:
    """Cheap mixed Chinese/English token estimate for budget telemetry.

    This is intentionally approximate. It is used for routing and warnings, not
    billing. Chinese-heavy prompts are usually near 1-2 chars/token; English
    and markdown are looser, so 1.8 chars/token is a conservative middle.
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 1.8))


def estimate_budget(
    *,
    prompt_text: str,
    selected_providers: list[str] | tuple[str, ...] | None = None,
    expected_output_tokens: int = 8000,
) -> dict[str, Any]:
    providers = [p for p in (selected_providers or []) if p]
    prompt_tokens = estimate_text_tokens(prompt_text)
    provider_count = len(providers)
    fanout_prompt_tokens = prompt_tokens * max(provider_count, 1)
    estimated_total_tokens = fanout_prompt_tokens + expected_output_tokens * max(provider_count, 1)
    level = "ok"
    if prompt_tokens >= PROMPT_ERROR_TOKENS or fanout_prompt_tokens >= FANOUT_ERROR_TOKENS:
        level = "error"
    elif prompt_tokens >= PROMPT_WARN_TOKENS or fanout_prompt_tokens >= FANOUT_WARN_TOKENS:
        level = "warn"
    return {
        "level": level,
        "prompt_chars": len(prompt_text or ""),
        "prompt_tokens_est": prompt_tokens,
        "selected_providers": providers,
        "provider_count": provider_count,
        "fanout_prompt_tokens_est": fanout_prompt_tokens,
        "expected_output_tokens_est": expected_output_tokens * max(provider_count, 1),
        "estimated_total_tokens": estimated_total_tokens,
    }


def decide_provider_route(
    *,
    project_kind: str | None,
    task: str | None,
    workflow_sop: dict[str, Any] | None,
    use_provider_source: bool,
    login_confirmed: dict[str, bool] | None,
    skip_material_assemble: bool,
    request_text: str,
) -> dict[str, Any]:
    """Choose whether this turn should fan out to web providers.

    The policy follows information boundaries rather than role count:
    use fanout for material collection / divergent creative proposals, and use
    a single local generation path for serial transforms, repairs, and oversized
    prompts.
    """
    selected = [name for name, enabled in (login_confirmed or {}).items() if enabled]
    sop = workflow_sop or {}
    task_key = task or ""
    mode = sop.get("mode") or ""
    budget = estimate_budget(prompt_text=request_text, selected_providers=selected)
    base = {
        "project_kind": project_kind or "",
        "task": task_key,
        "sop_mode": mode,
        "selected_providers": selected,
        "provider_count": len(selected),
        "budget": budget,
    }

    if not use_provider_source:
        return {**base, "decision": "single_agent", "reason": "ai_provider_off", "boundary": "local_generation"}
    if not selected:
        return {**base, "decision": "single_agent", "reason": "no_provider_selected", "boundary": "local_generation"}
    if budget.get("level") == "error":
        return {**base, "decision": "single_agent", "reason": "fanout_budget_too_high", "boundary": "cost_guard"}
    if skip_material_assemble:
        return {**base, "decision": "fanout", "reason": "context_followup_provider_session", "boundary": "provider_context"}
    if mode == "serial_repair" or task_key in SINGLE_AGENT_TASKS:
        return {**base, "decision": "single_agent", "reason": "serial_task_low_information_boundary", "boundary": "confirmed_material_transform"}
    if mode == "serial_transform":
        return {**base, "decision": "single_agent", "reason": "serial_transform_confirmed_material", "boundary": "confirmed_material_transform"}
    if mode == "parallel_collect_then_serial_merge" or task_key in FANOUT_TASKS:
        return {**base, "decision": "fanout", "reason": "parallel_material_collection", "boundary": "divergent_material_collection"}
    if len((request_text or "").strip()) < 80:
        return {**base, "decision": "single_agent", "reason": "small_request_fast_path", "boundary": "local_generation"}
    return {**base, "decision": "fanout", "reason": "default_material_collection", "boundary": "divergent_material_collection"}


def check_request_text(
    *,
    project_kind: str | None,
    task: str | None,
    request_text: str,
    workflow_sop: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run deterministic prompt harness checks before provider fanout.

    The checks catch known failure modes, especially task/prompt mismatches that
    would send providers into a narrow answer shape before LangGraph can recover.
    """
    text = request_text or ""
    issues: list[dict[str, Any]] = []
    kind = project_kind or ""
    task_key = task or ""
    sop = workflow_sop or {}

    if not text.strip():
        issues.append(_issue("error", "empty_prompt", "provider 提问文本为空。"))

    raw_rules = [item for item in sop.get("hard_rules") or [] if _rule_text(item).strip()]
    hard_rules = [_rule_text(item) for item in raw_rules]
    missing_rules = [rule for rule in hard_rules if rule not in text]
    if hard_rules and missing_rules:
        issues.append(_issue(
            "warn",
            "sop_rules_not_embedded",
            f"SOP 硬规则有 {len(missing_rules)} 条未出现在 provider 提问包中。",
            {"missing_count": len(missing_rules)},
        ))

    if kind == SHORT_FILM_KIND:
        _check_short_film_prompt(task_key, text, issues)
    _check_sop_predicates(raw_rules, text, issues)

    level = "ok"
    if any(item["level"] == "error" for item in issues):
        level = "error"
    elif any(item["level"] == "warn" for item in issues):
        level = "warn"
    return {
        "ok": level != "error",
        "level": level,
        "issues": issues,
        "prompt_hash": text_hash(text),
        "prompt_chars": len(text),
        "prompt_tokens_est": estimate_text_tokens(text),
    }


def summarize_state_delta(node: str, delta: Any) -> dict[str, Any]:
    if not isinstance(delta, dict):
        return {"node": node, "type": type(delta).__name__, "preview": str(delta)[:160]}
    summary: dict[str, Any] = {"node": node, "keys": sorted(str(k) for k in delta.keys())}
    for key, value in delta.items():
        summary[str(key)] = summarize_value(value)
    return summary


def summarize_value(value: Any) -> Any:
    if isinstance(value, str):
        return {"type": "str", "chars": len(value), "hash": text_hash(value)}
    if isinstance(value, list):
        item = {"type": "list", "count": len(value)}
        if value and isinstance(value[0], dict):
            item["first_keys"] = sorted(str(k) for k in value[0].keys())
        return item
    if isinstance(value, dict):
        result: dict[str, Any] = {"type": "dict", "keys": sorted(str(k) for k in value.keys())[:20]}
        for name in ("ok", "level", "passed", "blocking_count", "provider_failed", "awaiting_provider_confirm"):
            if name in value:
                result[name] = value[name]
        if "draft" in value:
            result["draft"] = summarize_value(value.get("draft"))
        if "provider_answers" in value:
            result["provider_answers"] = summarize_value(value.get("provider_answers"))
        return result
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__, "preview": str(value)[:160]}


def _check_short_film_prompt(task: str, text: str, issues: list[dict[str, Any]]) -> None:
    output_section = _section(text, "## 六、输出要求")
    concept_guard = "概念任务应输出多项结构化剧本开发材料" in output_section
    bad_logline_limits = [
        r"一句话故事",
        r"不超过\s*50\s*字",
        r"50\s*字以内",
        r"完整的\s*logline",
        r"只输出.*logline",
    ]

    if task == "logline":
        has_concept_material = "概念开发材料" in text
        has_expandable_script = any(mark in text for mark in ("可扩展成剧本", "可扩展为剧本", "扩展成正式剧本", "扩展为短片剧本"))
        if not (has_concept_material and has_expandable_script):
            issues.append(_issue(
                "error",
                "short_film_concept_too_narrow",
                "短片概念任务缺少“概念开发材料/可扩展成剧本”约束，容易退化成单句 logline。",
            ))
        if any(re.search(pattern, output_section, flags=re.I) for pattern in bad_logline_limits) and not concept_guard:
            issues.append(_issue(
                "error",
                "short_film_concept_word_limit_conflict",
                "输出要求把短片概念压成单句/50字 logline，与概念开发任务冲突。",
            ))

    if task in {"screenplay", "prose"}:
        required = ["场景标题", "动作", "角色", "对白"]
        missing = [item for item in required if item not in text]
        if missing:
            issues.append(_issue(
                "error",
                "screenplay_format_missing",
                f"剧本任务缺少影视脚本格式要求：{','.join(missing)}。",
            ))

    if task == "shot_list":
        required = ["镜号", "景别", "画面", "声音"]
        missing = [item for item in required if item not in text]
        if missing:
            issues.append(_issue(
                "error",
                "shot_list_fields_missing",
                f"分镜任务缺少镜头执行字段：{','.join(missing)}。",
            ))


def _check_sop_predicates(rules: list[Any], text: str, issues: list[dict[str, Any]]) -> None:
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        predicate = rule.get("predicate") or {}
        if not isinstance(predicate, dict):
            continue
        ptype = predicate.get("type")
        severity = "error" if rule.get("severity") == "blocker" else "warn"
        code = str(rule.get("id") or ptype or "sop_predicate_failed")
        message = str(rule.get("text") or "SOP predicate 未通过。")
        if ptype == "prompt_contains_all":
            missing = [item for item in predicate.get("required") or [] if str(item) not in text]
            if missing:
                issues.append(_issue(severity, code, message, {"missing": missing}))
        elif ptype == "prompt_contains_any":
            required = [str(item) for item in predicate.get("required") or []]
            if required and not any(item in text for item in required):
                issues.append(_issue(severity, code, message, {"required_any": required}))
        elif ptype == "prompt_pattern_absent":
            scope_text = _predicate_scope(text, predicate.get("scope"))
            hits = [pattern for pattern in predicate.get("forbidden") or [] if re.search(str(pattern), scope_text, flags=re.I)]
            if hits:
                issues.append(_issue(severity, code, message, {"forbidden_hits": hits}))


def _predicate_scope(text: str, scope: str | None) -> str:
    if scope in {"output", "output_requirements"}:
        return _section(text, "## 六、输出要求")
    if scope in {"user", "user_request"}:
        return _section(text, "## 四、用户具体要求")
    return text


def _rule_text(rule: Any) -> str:
    if isinstance(rule, dict):
        return str(rule.get("text") or rule.get("id") or "")
    return str(rule)


def _section(text: str, marker: str) -> str:
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1]
    next_heading = re.search(r"\n##\s+", tail)
    return tail[:next_heading.start()] if next_heading else tail


def _issue(level: str, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    item = {"level": level, "code": code, "message": message}
    if details:
        item["details"] = details
    return item
