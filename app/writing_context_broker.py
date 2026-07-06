from __future__ import annotations

import json
from typing import Any


DEFAULT_TOKEN_BUDGET = 9000

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "base_setting": ("constraints", "project_docs", "long_term_settings"),
    "outline": ("chapter_outline", "outline_context"),
    "character": ("character_profiles",),
    "worldview": ("worldbuilding",),
    "plot": ("plot_notes",),
    "chapter_summary": ("cross_chapter", "output_recall"),
    "project_wiki": ("wiki_items", "project_wiki_items"),
    "writing_techniques": ("technique_context",),
    "methodology": ("methodology_context", "creative_preflight", "method_reference_results"),
    "creative_state": ("creative_state",),
    "reference_novels": (
        "semantic_results",
        "five_dim_results",
        "method_reference_results",
        "reference_cards",
        "reference_retrieval",
    ),
    "references": ("project_assets", "project_asset_excerpts", "source_doc_excerpts"),
    "packaging": ("packaging_context",),
    "research": ("research_brief",),
    "chapter_function": ("chapter_function",),
    "reader_experience": ("reader_experience",),
    "self_check": ("self_check_loop",),
    "humanization": ("humanization_check",),
}


def resolve_context(
    *,
    bundle: dict[str, Any],
    task: str | None = None,
    project_kind: str | None = None,
    token_budget: int | None = None,
) -> dict[str, Any]:
    """Normalize an assembled material bundle into brokered context blocks.

    The first landing is deliberately additive: existing resolvers still gather
    material, and this broker records source, requirement level, budget impact
    and trace so later nodes can consume a single shape.
    """
    profile = _profile(bundle)
    required_sections = set(str(item) for item in profile.get("material_sections") or [])
    budget = _budget(token_budget, bundle)
    blocks = _collect_blocks(bundle, required_sections=required_sections)
    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    used = 0
    for block in blocks:
        cost = int(block.get("estimated_tokens") or 0)
        required = block.get("requirement") == "required"
        if required or used + cost <= budget:
            selected.append(block)
            used += cost
        else:
            dropped.append({**_brief_block(block), "reason": "token_budget"})
    summary = {
        "ok": True,
        "task": task or bundle.get("task") or "",
        "project_kind": project_kind or bundle.get("project_kind") or "",
        "stage": profile.get("id") or "",
        "stage_label": profile.get("label") or "",
        "token_budget": budget,
        "estimated_tokens": used,
        "selected": len(selected),
        "dropped": len(dropped),
        "required_sections": sorted(required_sections),
    }
    return {
        "ok": True,
        "summary": summary,
        "blocks": [_brief_block(block) for block in selected],
        "dropped": dropped,
        "trace": [
            {
                "resolver": block.get("resolver"),
                "section": block.get("section"),
                "source": block.get("source"),
                "requirement": block.get("requirement"),
                "estimated_tokens": block.get("estimated_tokens"),
                "status": "selected",
            }
            for block in selected
        ] + [
            {
                "resolver": block.get("resolver"),
                "section": block.get("section"),
                "source": block.get("source"),
                "requirement": block.get("requirement"),
                "estimated_tokens": block.get("estimated_tokens"),
                "status": "dropped",
                "reason": block.get("reason"),
            }
            for block in dropped
        ],
    }


def attach_context_broker(
    bundle: dict[str, Any],
    *,
    task: str | None = None,
    project_kind: str | None = None,
    token_budget: int | None = None,
    enforce: bool = True,
) -> dict[str, Any]:
    resolution = resolve_context(
        bundle=bundle,
        task=task,
        project_kind=project_kind,
        token_budget=token_budget,
    )
    out = dict(bundle or {})
    out["context_broker"] = resolution
    out["context_trace"] = resolution.get("trace") or []
    if enforce:
        out = _apply_context_boundary(out, resolution)
    return out


def _apply_context_boundary(bundle: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    """Prune known material inputs to the broker-selected set.

    Unknown operational fields stay untouched. Known creative material aliases
    are controlled by the broker so downstream prompt builders consume the same
    required/preferred + token-budget decision recorded in the trace.
    """
    selected_sources = {
        str(item.get("source") or "")
        for item in resolution.get("trace") or []
        if item.get("status") == "selected"
    }
    managed_keys = {key for aliases in SECTION_ALIASES.values() for key in aliases}
    out = dict(bundle)
    materials = out.get("materials") if isinstance(out.get("materials"), dict) else {}
    if isinstance(materials, dict):
        next_materials: dict[str, Any] = {}
        for key, value in materials.items():
            source = f"materials.{key}"
            if key == "project_docs" and isinstance(value, dict):
                kept_docs = {
                    name: text
                    for name, text in value.items()
                    if f"materials.project_docs.{name}" in selected_sources
                }
                if kept_docs:
                    next_materials[key] = kept_docs
                continue
            if key not in managed_keys or source in selected_sources:
                next_materials[key] = value
        out["materials"] = next_materials
    for key in managed_keys:
        if key in out and f"bundle.{key}" not in selected_sources:
            out.pop(key, None)
    summary = dict((out.get("context_broker") or {}).get("summary") or {})
    summary["enforced"] = True
    summary["managed_keys"] = len(managed_keys)
    summary["material_keys_after"] = len(out.get("materials") or {})
    out["context_broker"] = {**(out.get("context_broker") or {}), "summary": summary}
    return out


def _profile(bundle: dict[str, Any]) -> dict[str, Any]:
    profile = bundle.get("stage_profile") or (bundle.get("request_analysis") or {}).get("stage_profile") or {}
    return profile if isinstance(profile, dict) else {}


def _budget(token_budget: int | None, bundle: dict[str, Any]) -> int:
    if isinstance(token_budget, int) and token_budget > 0:
        return min(token_budget, 50_000)
    raw = (bundle.get("token_budget") or {}).get("material_tokens")
    try:
        value = int(raw)
        return min(max(value, 1200), 50_000)
    except Exception:
        return DEFAULT_TOKEN_BUDGET


def _collect_blocks(bundle: dict[str, Any], *, required_sections: set[str]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    materials = bundle.get("materials") if isinstance(bundle.get("materials"), dict) else {}
    seen: set[tuple[str, str]] = set()

    def add(section: str, key: str, value: Any, *, source: str, resolver: str) -> None:
        if _is_empty(value):
            return
        identity = (section, key)
        if identity in seen:
            return
        seen.add(identity)
        text = _summarize(value)
        blocks.append({
            "id": f"{section}:{key}",
            "section": section,
            "key": key,
            "title": _title(section, key),
            "source": source,
            "resolver": resolver,
            "requirement": "required" if section in required_sections else "preferred",
            "estimated_tokens": _estimate_tokens(value),
            "preview": text,
            "item_count": _item_count(value),
        })

    for section, aliases in SECTION_ALIASES.items():
        for key in aliases:
            if key in materials:
                add(section, key, materials.get(key), source=f"materials.{key}", resolver=f"{section}_resolver")
            if key in bundle:
                add(section, key, bundle.get(key), source=f"bundle.{key}", resolver=f"{section}_resolver")

    project_docs = materials.get("project_docs")
    if isinstance(project_docs, dict):
        for name, text in project_docs.items():
            section = _project_doc_section(name)
            add(section, f"project_docs:{name}", text, source=f"materials.project_docs.{name}", resolver="project_doc_resolver")
    return sorted(
        blocks,
        key=lambda item: (
            0 if item.get("requirement") == "required" else 1,
            str(item.get("section") or ""),
            str(item.get("key") or ""),
        ),
    )


def _project_doc_section(name: str) -> str:
    text = str(name or "").lower()
    if "outline" in text or "大纲" in text:
        return "outline"
    if "character" in text or "人物" in text:
        return "character"
    if "world" in text or "世界" in text:
        return "worldview"
    if "wiki" in text or "维基" in text:
        return "project_wiki"
    if "skill" in text or "技法" in text or "技能" in text:
        return "writing_techniques"
    return "references"


def _brief_block(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": block.get("id"),
        "section": block.get("section"),
        "key": block.get("key"),
        "title": block.get("title"),
        "source": block.get("source"),
        "resolver": block.get("resolver"),
        "requirement": block.get("requirement"),
        "estimated_tokens": block.get("estimated_tokens"),
        "item_count": block.get("item_count"),
        "preview": block.get("preview"),
    }


def _title(section: str, key: str) -> str:
    labels = {
        "base_setting": "基础设定",
        "outline": "大纲",
        "character": "人物",
        "worldview": "世界观",
        "plot": "情节",
        "chapter_summary": "章节记忆",
        "project_wiki": "项目 Wiki",
        "writing_techniques": "写作技法",
        "methodology": "方法论",
        "creative_state": "创作状态",
        "reference_novels": "参考小说",
        "references": "项目资产",
        "research": "事实细节",
        "chapter_function": "章节功能",
        "reader_experience": "读者体验",
        "self_check": "自检",
        "humanization": "人味表达",
    }
    return f"{labels.get(section, section)} / {key}"


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _item_count(value: Any) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1 if not _is_empty(value) else 0


def _estimate_tokens(value: Any) -> int:
    if isinstance(value, str):
        return max(1, len(value) // 2)
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)
    return max(1, len(text) // 2)


def _summarize(value: Any, limit: int = 240) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    text = " ".join(text.split())
    return text[:limit] + ("..." if len(text) > limit else "")
