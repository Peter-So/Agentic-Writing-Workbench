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
    creative_preflight: dict[str, Any] | None = None,
    methodology_context: dict[str, Any] | None = None,
    creative_enhancements: dict[str, Any] | None = None,
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
        if task_key in {"prose", "expansion", "fix"}:
            _require_any(
                text,
                ["动作", "对白", "视线", "停顿", "选择", "冲突"],
                issues,
                "reader_experience_signal",
                "正文缺少动作/对白/选择/冲突等读者体验信号，可能仍停留在说明层。",
            )
    for check in (creative_preflight or {}).get("checks") or []:
        if isinstance(check, dict) and check.get("level") == "warn":
            issues.append({
                "level": "warn",
                "code": f"preflight_{check.get('code')}",
                "message": check.get("message") or "创作前置检查存在警告。",
            })
    enhancements = creative_enhancements or {}
    reference_items = (enhancements.get("reference_cards") or {}).get("items") or []
    method_dims = {
        str(item.get("dimension") or "")
        for item in reference_items
        if isinstance(item, dict) and str(item.get("dimension") or "").startswith("method_")
    }
    if method_dims:
        if "method_motivation_chain" in method_dims:
            _require_any(
                text,
                ["为了", "必须", "代价", "选择", "阻碍", "想要"],
                issues,
                "method_motivation_chain_signal",
                "已召回动机链参考维度，但稿件中欲望/阻碍/代价/选择信号不足。",
            )
        if "method_reader_experience" in method_dims:
            _require_any(
                text,
                ["悬念", "压力", "紧张", "期待", "释放", "余味", "冲突"],
                issues,
                "method_reader_experience_signal",
                "已召回读者体验参考维度，但稿件中压力/悬念/释放/余味信号不足。",
            )
        if "method_chapter_function" in method_dims and task_key in {"outline", "beat_sheet", "prose", "expansion", "fix"}:
            _require_any(
                text,
                ["推进", "转折", "钩子", "回收", "升级", "揭示", "收束"],
                issues,
                "method_chapter_function_signal",
                "已召回章节功能参考维度，但稿件中章节推进/转折/回收功能不够明确。",
            )
        if "method_humanization" in method_dims and task_key in {"prose", "expansion", "fix"}:
            _require_any(
                text,
                ["“", "”", "停", "看", "皱", "沉默", "低声"],
                issues,
                "method_humanization_signal",
                "已召回人味表达参考维度，但稿件中对白、停顿或细微动作信号不足。",
            )
    if (enhancements.get("research_brief") or {}).get("needed"):
        issues.append({
            "level": "warn",
            "code": "research_brief_needed",
            "message": "本轮触发事实考据节点；若定稿包含具体事实，请确认有来源或标记为虚构边界。",
        })
    if kind == STRONG_NOVEL_KIND and task_key in {"logline", "brief", "setting", "outline"}:
        _require_any(
            text,
            ["读者", "卖点", "故事承诺", "核心命题", "主角", "阻碍"],
            issues,
            "packaging_signal",
            "前期规划稿缺少目标读者/卖点/故事承诺等包装与定位信号。",
        )
    if kind == STRONG_NOVEL_KIND and task_key in {"prose", "expansion", "fix"}:
        try:
            from app.creative_enhancements import anti_ai_flags

            flags = anti_ai_flags(text)
        except Exception:
            flags = []
        if flags:
            issues.append({
                "level": "warn",
                "code": "anti_ai_flavor_flags",
                "message": "正文存在疑似机械表达或空泛表达：" + "；".join(flags[:4]),
            })
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
        "methodology_context": methodology_context or {},
        "creative_preflight": creative_preflight or {},
        "creative_enhancements": creative_enhancements or {},
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
