from __future__ import annotations

import json
import re
from typing import Any

from app.novel_context import normalize_novel_id
from app.project_wiki import upsert_project_wiki_entry
from app.writing_task_profiles import is_novel_planning_task, novel_stage_profile


def chapter_material_entry_id(chapter: int | None, task: str = "prose") -> str:
    unit = f"ch{int(chapter):02d}" if chapter else "nounit"
    return f"material-{task}-{unit}"


def build_chapter_material_index(
    *,
    novel_id: str | None,
    task: str,
    chapter: int | None,
    message: str,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """Build a chapter-scoped material index for routing and provider packets.

    The index is a project Wiki artifact. It records precise chapter-facing
    material and local-only reference pointers. External providers receive only
    the `external_packet` fields, never the local reference snippets.
    """
    nid = normalize_novel_id(novel_id)
    analysis = bundle.get("request_analysis") or {}
    stage_profile = bundle.get("stage_profile") or analysis.get("stage_profile") or {}
    materials = bundle.get("materials") or {}
    outline = _target_outline(bundle, chapter)
    characters = _character_names(bundle, message, outline)
    summaries = _cross_chapter_summaries(bundle.get("cross_chapter"))
    if not summaries and chapter and chapter > 1:
        summaries = _fallback_cross_chapter_summaries(nid, chapter, characters, message)
    profiles = _character_profiles(nid, characters)
    style_rules = _external_style_rules(task)
    technique_brief = _technique_brief(bundle, message, outline)
    local_refs = _local_reference_pointers(materials)
    prose_locations = _prose_locations(bundle, analysis)
    target_prose = _target_prose_block(prose_locations)
    index = {
        "version": 1,
        "novel_id": nid,
        "project_kind": bundle.get("project_kind") or "",
        "task": task,
        "creative_stage": stage_profile.get("id") or analysis.get("creative_stage") or "",
        "stage_profile": stage_profile,
        "chapter": chapter,
        "query": message,
        "external_packet": {
            "task": _task_sentence(task, chapter, message, analysis),
            "target_outline": outline,
            "history_summary": summaries,
            "characters": profiles,
            "target_prose": target_prose,
            "techniques": technique_brief,
            "style_rules": style_rules,
        },
        "local_only": {
            "reference_pointers": local_refs,
            "intent": {
                "task": analysis.get("task") or task,
                "creative_stage": analysis.get("creative_stage") or stage_profile.get("id") or "",
                "target_chapter": analysis.get("target_chapter") or chapter,
                "affected_materials": analysis.get("affected_materials") or [],
                "affected_files": analysis.get("affected_files") or [],
                "prose_locations": prose_locations,
            },
        },
    }
    _persist_index(nid, task, chapter, index)
    return index


def format_provider_packet(index: dict[str, Any]) -> str:
    packet = index.get("external_packet") or {}
    task = str(index.get("task") or "")
    novel_planning = is_novel_planning_task(str(index.get("project_kind") or ""), task)
    parts = []
    if packet.get("target_outline"):
        title = "### 1. 目标结构材料" if novel_planning else "### 1. 本章大纲"
        parts.append(title + "\n" + packet["target_outline"])
    if packet.get("history_summary"):
        title = "### 2. 已有连续性材料" if novel_planning else "### 2. 前文关联与伏笔"
        parts.append(title + "\n" + packet["history_summary"])
    if packet.get("characters"):
        title = "### 3. 相关人物材料" if novel_planning else "### 3. 本章相关人物"
        parts.append(title + "\n" + packet["characters"])
    if packet.get("target_prose"):
        parts.append("### 4. 待改正文定位\n" + packet["target_prose"])
    if packet.get("techniques"):
        parts.append("### 5. 参考技法\n" + packet["techniques"])
    if packet.get("style_rules"):
        parts.append("### 6. 写作边界\n" + packet["style_rules"])
    return "\n\n".join(parts)


def _persist_index(novel_id: str, task: str, chapter: int | None, index: dict[str, Any]) -> None:
    try:
        title = f"第{chapter}章材料索引" if chapter else f"{task}材料索引"
        upsert_project_wiki_entry(
            novel_id,
            entry_id=chapter_material_entry_id(chapter, task),
            title=title,
            content=json.dumps(index, ensure_ascii=False, indent=2),
            category="material",
            source="chapter_material_index",
            task=task,
            tags=[task, *( [f"第{chapter}章"] if chapter else [] ), "材料索引", "信息边界"],
        )
    except Exception:
        pass


def _target_outline(bundle: dict[str, Any], chapter: int | None) -> str:
    text = str((bundle.get("materials") or {}).get("chapter_outline") or "").strip()
    if not text:
        return ""
    if chapter:
        try:
            from app.outline_writeback import clean_outline_archive_content

            text = clean_outline_archive_content(text, chapter) or text
        except Exception:
            pass
        block = _extract_chapter_block(text, chapter)
        if block:
            text = block
    return _clip(_drop_local_source_notes(text), 7000)


def _extract_chapter_block(text: str, chapter: int) -> str:
    cn = _cn_num(chapter)
    pattern = re.compile(
        rf"(^#{{1,6}}\s*(?:第\s*)?(?:{chapter}|{cn})\s*章[^\n]*\n.*?)(?=^#{{1,6}}\s*(?:第\s*)?(?:\d+|[一二三四五六七八九十百两]+)\s*章|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def _cross_chapter_summaries(items: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    for item in items or []:
        chapter = item.get("chapter")
        relation = "上一章" if item.get("relation") == "adjacent" else "关联章"
        bits = []
        for key, label, count in [
            ("events", "已发生", 5),
            ("open_threads", "未解决", 4),
            ("character_changes", "人物变化", 4),
            ("facts", "设定增量", 3),
        ]:
            vals = [str(v) for v in (item.get(key) or []) if str(v).strip()]
            if vals:
                bits.append(f"- {label}：" + "；".join(vals[:count]))
        if bits:
            lines.append(f"#### 第{chapter}章（{relation}）\n" + "\n".join(bits))
    return _clip("\n\n".join(lines), 2600)


def _fallback_cross_chapter_summaries(
    novel_id: str,
    chapter: int,
    characters: list[str],
    message: str,
) -> str:
    try:
        from app.chapter_summary import relevant_summaries

        items = relevant_summaries(chapter, characters=characters, hints=message, novel_id=novel_id)
    except Exception:
        items = []
    return _cross_chapter_summaries(items)


def _character_profiles(novel_id: str, names: list[str]) -> str:
    if not names:
        return ""
    try:
        from app.project_structure import resolve_structure_target

        _role, path = resolve_structure_target(novel_id, "character", create_missing=False)
    except Exception:
        path = None
    if not path or not path.exists():
        return ""
    full = path.read_text(encoding="utf-8", errors="replace")
    snippets = []
    for name in names[:10]:
        section = _section_for_name(full, name)
        if section:
            snippets.append(_clip(_drop_local_source_notes(section), 900))
    return _clip("\n\n---\n\n".join(snippets), 4800)


def _section_for_name(text: str, name: str) -> str:
    pattern = re.compile(rf"(#{{1,6}}\s*[^\n#]*{re.escape(name)}[^\n]*\n.*?)(?=\n#{{1,6}}\s|\Z)", re.DOTALL)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if name in line:
            start = max(0, idx - 2)
            end = min(len(lines), idx + 8)
            return f"### {name}\n" + "\n".join(lines[start:end]).strip()
    return ""


def _character_names(bundle: dict[str, Any], message: str, outline: str) -> list[str]:
    analysis = bundle.get("request_analysis") or {}
    candidates = []
    involved = re.search(r"###\s*涉及人物\s*\n(?P<body>.*?)(?=\n---|\n#{1,6}\s|\Z)", outline or "", re.DOTALL)
    if involved:
        body = involved.group("body")
        for chunk in re.split(r"[、，,。\n（）()·\s]+", body):
            if chunk:
                candidates.append(chunk)
    for key in ("entities", "characters", "people"):
        value = analysis.get(key)
        if isinstance(value, list):
            candidates.extend(str(item) for item in value)
    candidates.extend(re.findall(r"[\u4e00-\u9fff]{2,4}", f"{message}\n{outline}"))
    stop = {
        "第三章", "第二章", "第一章", "本章", "大纲", "人物", "场景", "动作", "主要", "事件",
        "源文档", "技法", "根据", "内容", "材料", "编写", "正文", "周一", "周四", "晨读",
        "班主任", "公告栏", "申请书", "推荐人", "教室", "窗帘", "黑板", "电话亭",
        "申请人", "主持", "公布", "名单", "隔壁班", "初次", "出现",
    }
    seen: set[str] = set()
    names: list[str] = []
    for raw in candidates:
        name = re.sub(r"[^\u4e00-\u9fff]", "", raw)
        if len(name) < 2 or len(name) > 4 or name in stop or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= 12:
            break
    return names


def _external_style_rules(task: str) -> str:
    if is_novel_planning_task("novel_strong", task):
        profile = novel_stage_profile(task)
        sections = "、".join(profile.get("material_sections") or [])
        base = [
            "只完成当前阶段的结构稿，不写正文成稿。",
            "不得引入与用户目标和项目结构材料无关的新设定。",
            "不要解释创作过程，不要列出本地资料来源。",
            "输出必须可直接归档到对应结构文件，不混入修改建议、优化要点或过程说明。",
        ]
        if sections:
            base.append(f"材料优先参考：{sections}。")
        return "\n".join(f"- {item}" for item in base)
    base = [
        "只写当前章节/当前任务，不扩写全书设定。",
        "不得引入本章大纲和人物设定之外的新世界观。",
        "不要解释创作过程，不要列出本地资料来源。",
        "少写抽象心理判断，多写动作、对白、场景和物件。",
        "禁止段尾升华、总结主题或替人物解释潜台词。",
    ]
    if task in {"prose", "expansion"}:
        base.append("正文按章节叙事输出，保持连续场景推进。")
    return "\n".join(f"- {item}" for item in base)


def _local_reference_pointers(materials: dict[str, Any]) -> list[dict[str, Any]]:
    pointers: list[dict[str, Any]] = []
    for key in ("semantic_results", "five_dim_results", "source_doc_excerpts"):
        for item in (materials.get(key) or [])[:8]:
            if not isinstance(item, dict):
                continue
            pointers.append({
                "source": key,
                "book": item.get("book") or item.get("novel") or item.get("source") or "",
                "dimension": item.get("dimension") or item.get("type") or "",
                "hint": _clip(item.get("text") or item.get("content") or item.get("excerpt") or "", 160),
            })
    return pointers


def _prose_locations(bundle: dict[str, Any], analysis: dict[str, Any]) -> list[dict[str, Any]]:
    locations = bundle.get("prose_locations") or analysis.get("prose_locations") or []
    if not isinstance(locations, list):
        return []
    out: list[dict[str, Any]] = []
    for item in locations:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        out.append({
            "role": item.get("role") or "chapter_body",
            "path": path,
            "chapter": item.get("chapter"),
            "start_line": item.get("start_line"),
            "end_line": item.get("end_line"),
            "match": item.get("match") or item.get("matched") or "",
            "anchor": item.get("anchor") or item.get("matched") or "",
            "score": item.get("score"),
            "excerpt": _clip(item.get("excerpt") or "", 1000),
        })
        if len(out) >= 5:
            break
    return out


def _target_prose_block(locations: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for idx, loc in enumerate(locations[:5], start=1):
        excerpt = _clip(_drop_local_source_notes(loc.get("excerpt") or ""), 900)
        if not excerpt:
            continue
        start = loc.get("start_line") or "?"
        end = loc.get("end_line") or "?"
        lines.append(f"#### 片段 {idx}（第 {start}-{end} 行）\n{excerpt}")
    return _clip("\n\n".join(lines), 3600)


def _technique_brief(bundle: dict[str, Any], message: str, outline: str) -> str:
    """Match each beat to technique laws from the writing technique KB.

    External providers receive only knowledge-base technique names and laws.
    The output deliberately avoids project nouns, objects, actions, source
    snippets, and reference labels. If deterministic matching is too weak, the
    matcher may ask an LLM to select technique IDs from the same knowledge base.
    """
    model_key = (bundle.get("model_preferences") or {}).get("review")
    try:
        from app.writing_techniques import match_techniques_for_beats

        lines = match_techniques_for_beats(
            outline,
            query=message,
            limit_beats=6,
            techniques_per_beat=2,
            model_key=model_key,
        )
    except Exception:
        lines = []
    cleaned: list[str] = []
    seen: set[str] = set()
    for line in lines:
        item = _sanitize_kb_technique_line(line)
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(f"- {item}")
        if len(cleaned) >= 8:
            break
    return _clip("\n".join(cleaned), 1800)


def _sanitize_kb_technique_line(line: str) -> str:
    """Keep only beat ordinals and KB technique laws in provider output."""
    text = re.sub(r"\s+", " ", str(line or "")).strip()
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"《[^》]+》", "", text)
    return _clip(text, 360)


def _task_sentence(task: str, chapter: int | None, message: str, analysis: dict[str, Any]) -> str:
    instruction = str(analysis.get("generator_instruction") or "").strip()
    if instruction:
        instruction = _strip_internal_labels(instruction)
        if instruction:
            return instruction
    if chapter and task == "prose":
        return f"根据第{chapter}章大纲和前情，创作第{chapter}章正文。"
    return message or "按当前材料完成本次写作任务。"


def _strip_internal_labels(text: str) -> str:
    text = re.sub(r"LLM\s*分析[后得出的]*", "", text, flags=re.I)
    return text.strip(" ：:;；")


def _drop_local_source_notes(text: str) -> str:
    text = re.sub(r"\[五维·[^\]]+\]", "", text)
    text = re.sub(r"\[技法·[^\]]+\]", "", text)
    text = re.sub(r"\[源文档·[^\]]+\]", "", text)
    text = re.sub(r"^\s*[-*]?\s*\*\*?参考机制\*\*?[：:].*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]?\s*参考机制[：:].*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"### 技法来源[\s\S]*?(?=\n---|\n### |\Z)", "", text)
    text = re.sub(r"本轮优化要点[:：][\s\S]*?(?=\n---|\n### |\Z)", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + f"\n（已截断，原文 {len(value)} 字）"


def _cn_num(n: int) -> str:
    nums = "零一二三四五六七八九十"
    if 0 <= n <= 10:
        return nums[n]
    if n < 20:
        return "十" + nums[n - 10]
    if n < 100:
        ten, one = divmod(n, 10)
        return nums[ten] + "十" + (nums[one] if one else "")
    return str(n)
