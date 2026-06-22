from __future__ import annotations

import json
import re
from typing import Any

from app.config import load_runtime_config
from app.llm_client import create_llm, resolve_text_model
from app.novel_context import normalize_novel_id
from app.project_kinds import STRONG_NOVEL_KIND, project_kind
from app.project_structure import find_related_structure_file, load_project_structure, role_for_task


def analyze_project_impact(
    *,
    task: str,
    chapter: int | None,
    accepted: str,
    novel_id: str | None,
    request_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze which project-structure files are affected by confirmed output.

    The contract is generic: targets are structure roles/paths, not project IDs or
    legacy filenames. Strong-novel chapter prose is represented as the virtual
    target ``chapter_body`` because it is archived through a separate explicit
    flow.
    """
    nid = normalize_novel_id(novel_id)
    kind = project_kind(nid)
    fallback = heuristic_project_impact(
        task=task,
        chapter=chapter,
        accepted=accepted,
        novel_id=nid,
        request_analysis=request_analysis,
    )
    try:
        cfg = load_runtime_config()
        model_key = resolve_text_model(cfg, "review", (request_analysis or {}).get("review_model"))
        llm = create_llm(cfg, model_key, temperature=0, max_tokens=1400, timeout=45, max_retries=1)
        raw = getattr(llm.invoke(_impact_prompt(nid, kind, task, chapter, accepted, request_analysis or {}, fallback)), "content", "") or ""
        parsed = _parse_json(raw)
        normalized = _normalize_plan(parsed, fallback)
        normalized["source"] = "llm"
        normalized["model_key"] = model_key
        return normalized
    except Exception as exc:
        fallback["source"] = "heuristic"
        fallback["error"] = f"{type(exc).__name__}: {exc}"
        return fallback


def heuristic_project_impact(
    *,
    task: str,
    chapter: int | None,
    accepted: str,
    novel_id: str | None,
    request_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    kind = project_kind(nid)
    structure = load_project_structure(nid)
    docs = structure.get("documents") or {}
    text = accepted or ""
    analysis = request_analysis or {}
    targets: list[str] = []

    primary_role = role_for_task(task, kind)
    if primary_role and primary_role in docs:
        targets.append(primary_role)
    elif task in {"prose", "expansion"} and kind == STRONG_NOVEL_KIND:
        targets.append("chapter_body")

    for value in _clean_list(analysis.get("affected_materials")) + _clean_list(analysis.get("affected_files")):
        role = _resolve_role(nid, value)
        if role and role not in targets:
            targets.append(role)

    keyword_roles = _keyword_roles(kind)
    for role, words in keyword_roles:
        if role in docs and any(word in text for word in words) and role not in targets:
            targets.append(role)

    if chapter and kind == STRONG_NOVEL_KIND and any(word in text for word in ["正文", "段落", "场景", "对白", "改写"]):
        if "chapter_body" not in targets:
            targets.append("chapter_body")

    if not targets:
        fallback_role = primary_role if primary_role in docs else next(iter(docs.keys()), "")
        if fallback_role:
            targets.append(fallback_role)

    primary_targets = targets[:1]
    related_targets = targets[1:]
    changes = [
        _change_for_target(target, target in primary_targets, task, chapter, text, analysis, docs, kind)
        for target in targets
    ]
    return {
        "ok": True,
        "source": "heuristic",
        "novel_id": nid,
        "project_kind": kind,
        "task": task,
        "chapter": chapter,
        "summary": "根据项目结构、请求分析和确认稿内容推断影响范围。",
        "primary_targets": primary_targets,
        "related_targets": related_targets,
        "changes": changes,
        "requires_manual_confirm": [item["target"] for item in changes if not item.get("auto_apply")],
    }


def _impact_prompt(
    novel_id: str,
    kind: str,
    task: str,
    chapter: int | None,
    accepted: str,
    request_analysis: dict[str, Any],
    fallback: dict[str, Any],
) -> str:
    structure = load_project_structure(novel_id)
    docs = structure.get("documents") or {}
    options = [
        {
            "role": role,
            "label": spec.get("label") or role,
            "path": spec.get("path") or "",
            "description": spec.get("description") or "",
        }
        for role, spec in docs.items()
    ]
    if kind == STRONG_NOVEL_KIND:
        options.append({"role": "chapter_body", "label": "章节正文", "path": "正文/chapter-XX.md", "description": "章节正文，必须显式归档或覆盖。"})
    schema = {
        "summary": "一句话说明本轮确认稿影响范围",
        "changes": [
            {
                "target": "必须是 options 里的 role 或 path",
                "role": "primary|related",
                "operation": "append|replace_section|manual_confirm",
                "reason": "为什么需要修改该结构文件",
                "patch": "适合写入该文件的短补丁，80-600字；不要复制完整回答",
                "auto_apply": "true 仅限主目标且 append 安全；关联材料、章节正文、覆盖式替换必须 false",
            }
        ],
    }
    return "\n".join([
        "你是创作项目的通用文件影响面分析器。用户已确认采纳一段产出，请判断哪些项目结构文件需要更新。",
        "硬规则：",
        "- 只输出 JSON，不要 markdown。",
        "- target 必须来自项目结构 options 的 role 或 path；不要输出固定项目编号或历史文件名。",
        "- 不要只选单文件；若人物/情节/设定/大纲/剧本/分镜会互相牵连，必须列出 related。",
        "- 只有主目标且 append 安全的结构文件可以 auto_apply=true。",
        "- 章节正文、分镜图资产、覆盖式替换、所有 related 默认 auto_apply=false。",
        "- patch 只写适合落入该文件的短文本，不要复制完整回答或解释本轮优化要点。",
        "",
        f"输出 schema：{json.dumps(schema, ensure_ascii=False)}",
        f"项目：{novel_id} / {kind}",
        f"任务 task：{task}",
        f"章节/场次 chapter：{chapter or ''}",
        f"项目结构 options：{json.dumps(options, ensure_ascii=False)[:5000]}",
        f"请求分析：{json.dumps(request_analysis, ensure_ascii=False)[:3000]}",
        f"启发式初判：{json.dumps(fallback, ensure_ascii=False)[:3000]}",
        f"确认内容：{accepted[:8000]}",
    ])


def _normalize_plan(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return fallback
    docs = (load_project_structure(fallback.get("novel_id")).get("documents") or {})
    allowed = set(docs.keys()) | {"chapter_body"}
    changes = []
    seen: set[str] = set()
    for item in parsed.get("changes") or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or "").strip()
        role = target if target in allowed else _resolve_role(fallback.get("novel_id"), target)
        if not role or role not in allowed:
            continue
        if role in seen:
            continue
        seen.add(role)
        operation = str(item.get("operation") or "manual_confirm")
        if operation not in {"append", "replace_section", "manual_confirm"}:
            operation = "manual_confirm"
        item_role = "primary" if str(item.get("role") or "").strip() == "primary" and not changes else "related"
        auto_apply = bool(item.get("auto_apply")) and item_role == "primary" and operation == "append" and role != "chapter_body"
        changes.append({
            "target": role,
            "label": _target_label(role, docs),
            "target_path": _target_path(role, docs),
            "role": item_role,
            "operation": operation,
            "reason": str(item.get("reason") or "确认稿影响该材料，需要人工确认是否同步。")[:800],
            "patch": _short_patch(str(item.get("patch") or "")),
            "auto_apply": auto_apply,
        })
    if not changes:
        return fallback
    changes[0]["role"] = "primary"
    return {
        **fallback,
        "summary": str(parsed.get("summary") or fallback.get("summary") or "")[:500],
        "primary_targets": [item["target"] for item in changes if item["role"] == "primary"],
        "related_targets": [item["target"] for item in changes if item["role"] != "primary"],
        "changes": changes,
        "requires_manual_confirm": [item["target"] for item in changes if not item.get("auto_apply")],
    }


def _change_for_target(
    target: str,
    primary: bool,
    task: str,
    chapter: int | None,
    text: str,
    analysis: dict[str, Any],
    docs: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    operation = "append"
    if target == "chapter_body":
        operation = "manual_confirm"
    elif task == "outline" and chapter and target in {"outline", "beat_sheet"}:
        operation = "replace_section"
    auto_apply = primary and operation == "append" and target != "chapter_body"
    return {
        "target": target,
        "label": _target_label(target, docs),
        "target_path": _target_path(target, docs),
        "role": "primary" if primary else "related",
        "operation": operation,
        "reason": _reason_for_target(target, task, analysis, docs, kind),
        "patch": _short_patch(text),
        "auto_apply": auto_apply,
    }


def _keyword_roles(kind: str) -> list[tuple[str, list[str]]]:
    if kind == "short_film":
        return [
            ("character", ["人物", "角色", "主角", "弧光", "关系"]),
            ("beat_sheet", ["节拍", "剧情", "冲突", "反转", "转折"]),
            ("screenplay", ["剧本", "场景", "对白", "动作"]),
            ("shot_list", ["分镜", "镜头", "景别", "画面"]),
            ("concept", ["概念", "logline", "主题", "命题"]),
            ("style", ["风格", "影像", "声音", "剪辑"]),
        ]
    if kind == "generic":
        return [
            ("inbox", ["随想", "记录", "材料", "素材"]),
            ("ideas", ["灵感", "结构", "问题", "方向"]),
            ("draft", ["草稿", "正文", "扩写", "改写"]),
            ("references", ["参考", "引用", "资料"]),
        ]
    return [
        ("character", ["人物", "角色", "主角", "配角", "欲望", "弧光", "关系"]),
        ("worldview", ["世界观", "规则", "时间线", "空间", "禁忌", "制度", "设定"]),
        ("plot", ["情节", "剧情", "节拍", "伏笔", "冲突", "反转", "钩子"]),
        ("outline", ["大纲", "章节", "主线", "分卷"]),
        ("base_setting", ["主题", "立意", "基调", "类型", "命题", "简报"]),
    ]


def _resolve_role(novel_id: str | None, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in {"章节正文", "正文", "chapter_body"}:
        return "chapter_body"
    try:
        docs = (load_project_structure(novel_id).get("documents") or {})
        if text in docs:
            return text
        raw_name = text.split("/")[-1].split("\\")[-1]
        for role, spec in docs.items():
            names = {str(spec.get("path") or ""), str(spec.get("canonical_path") or ""), str(spec.get("label") or "")}
            names.update(str(item or "") for item in (spec.get("aliases") or []))
            basenames = {name.split("/")[-1].split("\\")[-1] for name in names if name}
            if text in names or raw_name in basenames:
                return role
        matched = find_related_structure_file(novel_id, text)
        if matched:
            return matched[0]
    except Exception:
        pass
    return text


def _target_path(role: str, docs: dict[str, Any]) -> str:
    if role == "chapter_body":
        return "正文/chapter-XX.md"
    return str((docs.get(role) or {}).get("path") or role)


def _target_label(role: str, docs: dict[str, Any]) -> str:
    if role == "chapter_body":
        return "章节正文"
    return str((docs.get(role) or {}).get("label") or role)


def _reason_for_target(target: str, task: str, analysis: dict[str, Any], docs: dict[str, Any], kind: str) -> str:
    label = "章节正文" if target == "chapter_body" else (docs.get(target) or {}).get("label") or target
    reason = analysis.get("impact_reason") or analysis.get("reason") if isinstance(analysis, dict) else ""
    return str(reason or f"本轮 {task} 确认稿可能影响「{label}」，需要同步或人工确认。")[:300]


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text[:120])
    return out[:12]


def _short_patch(text: str) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    return clean[:1200]


def _parse_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL) or re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(1) if match.re.groups else match.group(0)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
