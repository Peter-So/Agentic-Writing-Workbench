from __future__ import annotations

from typing import Any

from app.project_kinds import SHORT_FILM_KIND, STRONG_NOVEL_KIND
from app.writing_task_profiles import is_novel_planning_task, novel_stage_profile


def decide_review_strategy(
    *,
    project_kind: str | None,
    task: str | None,
    draft: str,
    need_audit: dict[str, Any] | None = None,
    request_harness: dict[str, Any] | None = None,
    token_budget: dict[str, Any] | None = None,
    provider_route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = project_kind or ""
    task_key = task or ""
    risks = _risk_labels(need_audit, request_harness, token_budget, provider_route)
    if kind == STRONG_NOVEL_KIND:
        if is_novel_planning_task(kind, task_key):
            return {
                "mode": "deterministic_checklist",
                "model": "none",
                "reason": "小说前期概念/设定/人物/大纲属于结构规划稿，只做低成本结构完整性检查，不触发正文级跨模型审查。",
                "risks": risks,
            }
        return {
            "mode": "cross_model",
            "model": "gpt",
            "reason": "001 小说强规范项目保持生成/审查模型分离。",
            "risks": risks,
        }
    if kind == SHORT_FILM_KIND:
        if task_key in {"screenplay", "logline", "shot_list"} or risks:
            return {
                "mode": "deterministic_checklist",
                "model": "none",
                "reason": "短片项目先做低成本格式/交付物检查；需要人工确认时再升级模型审查。",
                "risks": risks,
            }
        return {
            "mode": "skip",
            "model": "none",
            "reason": "低风险短片中间材料不触发额外模型审查。",
            "risks": risks,
        }
    return {
        "mode": "skip",
        "model": "none",
        "reason": "通用项目默认不额外消耗审查模型。",
        "risks": risks,
    }


def deterministic_review(
    *,
    project_kind: str | None,
    task: str | None,
    draft: str,
    strategy: dict[str, Any],
    technique_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = draft or ""
    kind = project_kind or ""
    task_key = task or ""
    issues: list[dict[str, Any]] = []
    if kind == STRONG_NOVEL_KIND:
        profile = novel_stage_profile(task_key)
        signals = profile.get("acceptance_signals") or []
        if signals and profile.get("flow") != "full_generation":
            _require_any(
                text,
                signals,
                issues,
                f"novel_{profile.get('id')}_signal",
                f"{profile.get('label') or '小说前期'}稿缺少可验收结构信号：{', '.join(signals[:8])}。",
            )
    if kind == SHORT_FILM_KIND:
        if task_key == "screenplay":
            _require(text, ["场景", "动作", "对白"], issues, "screenplay_basic_format")
            _require_any(text, ["停顿", "视线", "声音", "道具", "沉默"], issues, "screenplay_craft_signal",
                         "剧本缺少可拍摄的动作/声音/停顿/道具信号，技法可能仍停留在说明层。")
            if len(text) < 800:
                issues.append({"level": "warn", "code": "screenplay_too_short", "message": "剧本文本较短，可能仍停留在梗概而非正式剧本。"})
        elif task_key == "logline":
            _require(text, ["主题", "主角", "阻碍", "代价"], issues, "concept_material_fields")
            if len(text) < 300:
                issues.append({"level": "warn", "code": "concept_too_narrow", "message": "概念材料较短，可能不足以支撑后续正式剧本。"})
        elif task_key == "shot_list":
            _require(text, ["镜号", "景别", "画面", "声音"], issues, "shot_list_fields")
    passed = not any(item.get("level") == "error" for item in issues)
    return {
        "ok": True,
        "model": "deterministic",
        "passed": passed,
        "overall_score": 100 if passed and not issues else 80 if passed else 50,
        "issues": issues,
        "strategy": strategy,
        "technique_context": technique_context or {},
    }


def _risk_labels(
    need_audit: dict[str, Any] | None,
    request_harness: dict[str, Any] | None,
    token_budget: dict[str, Any] | None,
    provider_route: dict[str, Any] | None,
) -> list[str]:
    risks: list[str] = []
    if (need_audit or {}).get("level") in {"warn", "error"}:
        risks.append(f"need_audit:{(need_audit or {}).get('level')}")
    if (request_harness or {}).get("level") in {"warn", "error"}:
        risks.append(f"harness:{(request_harness or {}).get('level')}")
    if (token_budget or {}).get("level") in {"warn", "error"}:
        risks.append(f"budget:{(token_budget or {}).get('level')}")
    if (provider_route or {}).get("reason") in {"fanout_budget_too_high", "serial_task_low_information_boundary"}:
        risks.append(f"route:{(provider_route or {}).get('reason')}")
    return risks


def _require(text: str, required: list[str], issues: list[dict[str, Any]], code: str) -> None:
    missing = [item for item in required if item not in text]
    if missing:
        issues.append({
            "level": "error",
            "code": code,
            "message": f"缺少字段/信号：{', '.join(missing)}。",
            "missing": missing,
        })


def _require_any(text: str, choices: list[str], issues: list[dict[str, Any]], code: str, message: str) -> None:
    if any(item in text for item in choices):
        return
    issues.append({
        "level": "warn",
        "code": code,
        "message": message,
        "expected_any": choices,
    })
