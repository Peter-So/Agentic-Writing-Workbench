from __future__ import annotations

import re
from typing import Any

from app.project_kinds import SHORT_FILM_KIND, STRONG_NOVEL_KIND


def build_creative_enhancements(
    *,
    project_kind: str | None,
    task: str | None,
    query: str = "",
    analysis: dict[str, Any] | None = None,
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build optional creative enhancement cards for the creation flow.

    The cards are abstract process aids. They do not copy reference excerpts into
    final prose, and they are later pruned by stage material profiles.
    """
    kind = str(project_kind or "")
    task_key = str(task or "").strip().lower()
    data = bundle or {}
    materials = data.get("materials") or {}
    analysis_data = analysis or data.get("request_analysis") or {}
    stage = str(analysis_data.get("creative_stage") or "").strip().lower()
    outline = "\n".join(filter(None, [
        str(materials.get("chapter_outline") or ""),
        str(materials.get("outline_context") or ""),
        str(materials.get("plot_notes") or ""),
    ]))
    source_text = "\n".join(filter(None, [query, outline, str(analysis_data.get("generator_instruction") or "")]))

    cards: dict[str, Any] = {}
    reference_cards = build_reference_decomposition_cards(
        materials,
        project_kind=kind,
        task=task_key,
        method_reference=data.get("method_reference_results") or {},
    )
    if reference_cards.get("items"):
        cards["reference_cards"] = reference_cards
    chapter_function = classify_chapter_function(source_text, project_kind=kind, task=task_key, creative_stage=stage)
    if chapter_function.get("available"):
        cards["chapter_function"] = chapter_function
    reader_experience = build_reader_experience_check(project_kind=kind, task=task_key, creative_stage=stage, text=source_text)
    if reader_experience.get("checks"):
        cards["reader_experience"] = reader_experience
    packaging = build_packaging_context(project_kind=kind, task=task_key, creative_stage=stage, text=source_text)
    if packaging.get("checks"):
        cards["packaging_context"] = packaging
    research = build_research_brief(project_kind=kind, task=task_key, creative_stage=stage, text=source_text)
    if research.get("needed"):
        cards["research_brief"] = research
    self_check = build_self_check_loop(project_kind=kind, task=task_key, creative_stage=stage)
    if self_check.get("steps"):
        cards["self_check_loop"] = self_check
    humanization = build_humanization_check(project_kind=kind, task=task_key)
    if humanization.get("checks"):
        cards["humanization_check"] = humanization

    return {
        "ok": True,
        "project_kind": kind,
        "task": task_key,
        "creative_stage": stage,
        "keys": list(cards.keys()),
        **cards,
    }


def format_enhancement_blocks(bundle: dict[str, Any]) -> str:
    sections: list[str] = []
    for key in (
        "reference_cards",
        "chapter_function",
        "reader_experience",
        "packaging_context",
        "research_brief",
        "self_check_loop",
        "humanization_check",
    ):
        item = bundle.get(key) or {}
        text = item.get("text") if isinstance(item, dict) else ""
        if text:
            sections.append(str(text))
    return "\n\n".join(sections)


def build_reference_decomposition_cards(
    materials: dict[str, Any],
    *,
    project_kind: str,
    task: str,
    method_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if project_kind != STRONG_NOVEL_KIND or task not in {"outline", "beat_sheet", "prose", "expansion", "fix"}:
        return {"ok": True, "items": [], "text": ""}
    method_items = []
    if isinstance(method_reference, dict):
        method_items = list(method_reference.get("results") or [])
    raw_items = method_items + list(materials.get("semantic_results") or []) + list(materials.get("five_dim_results") or [])
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items[:18]:
        if not isinstance(item, dict):
            continue
        book = str(item.get("book") or item.get("novel") or item.get("source") or "参考").strip()
        dim = str(item.get("dimension") or item.get("type") or "机制").strip()
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        key = f"{book}:{dim}"
        if key in seen:
            continue
        seen.add(key)
        cards.append({
            "source": book,
            "dimension": dim,
            "method_label": item.get("method_label") or _method_label_for_dimension(dim),
            "method_use": item.get("method_use") or _method_use_for_dimension(dim),
            "mechanism": _mechanism_for_dimension(dim, text),
            "use_as": _reference_use(task, dim),
            "avoid": "只取机制、节奏、情绪功能和避雷点，不复制人物、物品、名词、句子或桥段。",
        })
        if len(cards) >= 6:
            break
    text = _format_reference_cards(cards)
    return {"ok": True, "items": cards, "text": text}


def classify_chapter_function(text: str, *, project_kind: str, task: str, creative_stage: str) -> dict[str, Any]:
    if project_kind == SHORT_FILM_KIND and task in {"beat_sheet", "screenplay", "prose", "shot_list"}:
        labels = [
            ("开场建立", ["开场", "日常", "建立", "片头"]),
            ("触发选择", ["触发", "选择", "决定", "代价"]),
            ("冲突升级", ["冲突", "升级", "逼近", "对抗"]),
            ("反转揭示", ["反转", "揭示", "真相", "秘密"]),
            ("余味收束", ["结尾", "余味", "收束", "回望"]),
        ]
    elif project_kind == STRONG_NOVEL_KIND and (creative_stage in {"outline", "plot", "prose"} or task in {"outline", "beat_sheet", "prose", "fix"}):
        labels = [
            ("布局章", ["布局", "铺垫", "安排", "埋下", "线索"]),
            ("冲突章", ["冲突", "对抗", "战斗", "交锋", "危机"]),
            ("过渡章", ["过渡", "赶路", "转场", "准备", "调整"]),
            ("恢复章", ["恢复", "疗伤", "休整", "复盘", "缓和"]),
            ("信息揭示章", ["揭示", "真相", "秘密", "发现", "公告"]),
        ]
    else:
        return {"ok": True, "available": False, "text": ""}
    scores = []
    for label, words in labels:
        scores.append((sum(str(text or "").count(word) for word in words), label, words))
    scores.sort(reverse=True)
    best_score, label, words = scores[0]
    if best_score <= 0:
        label = "待判定"
    rules = {
        "布局章": ["只埋必要线索", "章末给可感知钩子", "避免大段解释设定"],
        "冲突章": ["压力逐级升级", "行动有代价", "结尾释放或转入更大压力"],
        "过渡章": ["过渡也要有信息变化", "用关系或目标变化代替流水账"],
        "恢复章": ["恢复段要改变人物关系或策略", "避免单纯复盘"],
        "信息揭示章": ["控制信息边界", "揭示要改变选择或风险"],
        "待判定": ["先明确本章功能", "功能决定节奏、材料和章末钩子"],
        "开场建立": ["快速建立主角处境", "用画面动作呈现主题"],
        "触发选择": ["选择必须有代价", "触发事件改变人物行动方向"],
        "冲突升级": ["每个节拍改变风险或关系", "避免重复争吵"],
        "反转揭示": ["反转改变观众对前文信息的理解", "避免口头解释真相"],
        "余味收束": ["结尾保留情绪回声", "不要用台词讲主题"],
    }.get(label, [])
    text_block = "\n".join([
        "## 章节/场次功能识别",
        f"- 识别结果：{label}",
        f"- 命中线索：{'、'.join(words)}" if best_score > 0 else "- 命中线索：不足，按待判定处理。",
        *[f"- 控制点：{rule}" for rule in rules],
        "使用要求：功能识别只决定节奏和材料取舍，不要作为正文解释输出。",
    ])
    return {"ok": True, "available": True, "label": label, "score": best_score, "rules": rules, "text": text_block}


def build_reader_experience_check(*, project_kind: str, task: str, creative_stage: str, text: str) -> dict[str, Any]:
    if project_kind == STRONG_NOVEL_KIND and task not in {"outline", "beat_sheet", "prose", "expansion", "fix"}:
        return {"ok": True, "checks": [], "text": ""}
    if project_kind == SHORT_FILM_KIND and task not in {"logline", "beat_sheet", "screenplay", "prose"}:
        return {"ok": True, "checks": [], "text": ""}
    checks = [
        "读者/观众能否在本轮内容里看到明确欲望和阻碍。",
        "是否存在压力源、延迟、释放或反转，而不是平铺信息。",
        "结尾是否留下余味、钩子或下一步行动压力。",
        "情绪是否由动作、对白、选择承载，而不是作者总结。",
    ]
    if project_kind == SHORT_FILM_KIND:
        checks.append("短片内容是否可拍：画面、动作、声音、道具是否足够明确。")
    text_block = "## 读者/观众体验检查\n" + "\n".join(f"- {item}" for item in checks) + "\n使用要求：作为验收检查，不写进定稿正文。"
    return {"ok": True, "checks": checks, "text": text_block}


def build_packaging_context(*, project_kind: str, task: str, creative_stage: str, text: str) -> dict[str, Any]:
    if project_kind == STRONG_NOVEL_KIND and task not in {"logline", "brief", "setting", "outline"}:
        return {"ok": True, "checks": [], "text": ""}
    if project_kind == SHORT_FILM_KIND and task not in {"logline", "outline"}:
        return {"ok": True, "checks": [], "text": ""}
    checks = [
        "一句话必须让人知道主角、欲望、阻碍和故事承诺。",
        "标题/简介方向优先体现反差、悬念、痛点或利益点。",
        "卖点包装不替代设定正文，包装内容要能回到项目材料。",
    ]
    text_block = "## 标题/简介/卖点包装检查\n" + "\n".join(f"- {item}" for item in checks) + "\n使用要求：只用于概念与对外表达，不强行写入正文。"
    return {"ok": True, "checks": checks, "text": text_block}


def build_research_brief(*, project_kind: str, task: str, creative_stage: str, text: str) -> dict[str, Any]:
    keywords = ["历史", "军事", "年代", "真实", "史实", "行业", "法律", "医学", "经济", "政策", "地理", "朝代", "战争"]
    hits = [word for word in keywords if word in (text or "")]
    if not hits:
        return {"ok": True, "needed": False, "text": ""}
    questions = [
        "哪些事实必须准确，哪些可以虚构。",
        "年代/制度/行业术语是否有可靠来源。",
        "人物行动是否受真实规则约束。",
        "输出中哪些内容需要标记为待考据。",
    ]
    text_block = "\n".join([
        "## 事实考据/研究节点",
        f"- 触发词：{'、'.join(hits[:8])}",
        *[f"- 待核查：{q}" for q in questions],
        "使用要求：没有可靠材料时不要编造具体事实；可以输出待考据项或降级为虚构边界说明。",
    ])
    return {"ok": True, "needed": True, "triggers": hits, "questions": questions, "text": text_block}


def build_self_check_loop(*, project_kind: str, task: str, creative_stage: str) -> dict[str, Any]:
    if project_kind not in {STRONG_NOVEL_KIND, SHORT_FILM_KIND}:
        return {"ok": True, "steps": [], "text": ""}
    if project_kind == STRONG_NOVEL_KIND:
        steps = [
            "确认阶段：概念/设定/大纲/情节/正文是否混写。",
            "确认材料：只使用本阶段允许材料，删掉无关引用。",
            "确认因果：动机、信息边界、收益代价是否闭合。",
            "确认体验：压力、释放、余味或钩子是否存在。",
            "确认定稿：删除来源标签、方法术语和过程说明。",
        ]
    else:
        steps = [
            "确认交付物：概念/角色/节拍/剧本/分镜是否明确。",
            "确认可拍性：画面、动作、声音、对白是否可执行。",
            "确认节奏：每个节拍是否改变信息、关系或风险。",
            "确认定稿：删除过程说明和解释性分析。",
        ]
    text_block = "## 生成前自检循环\n" + "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, 1)) + "\n使用要求：自检后只输出定稿内容。"
    return {"ok": True, "steps": steps, "text": text_block}


def build_humanization_check(*, project_kind: str, task: str) -> dict[str, Any]:
    if project_kind == STRONG_NOVEL_KIND and task not in {"prose", "expansion", "fix"}:
        return {"ok": True, "checks": [], "text": ""}
    if project_kind == SHORT_FILM_KIND and task not in {"screenplay", "prose", "fix"}:
        return {"ok": True, "checks": [], "text": ""}
    checks = [
        "删掉空泛环境铺垫、机械连接词和段尾升华。",
        "用具体动作、对白、停顿、误解、选择替代抽象情绪标签。",
        "句式长短交错，允许少量口语、不完整动作和真实停顿。",
        "避免把人物心理机制直接翻译给读者。",
    ]
    text_block = "## 去 AI 味/人味检查\n" + "\n".join(f"- {item}" for item in checks) + "\n使用要求：只用于修订语言质感，不作为正文说明输出。"
    return {"ok": True, "checks": checks, "text": text_block}


def anti_ai_flags(text: str) -> list[str]:
    flags = []
    patterns = {
        "empty_summary": ["这意味着", "标志着", "象征着", "仿佛", "宛如"],
        "mechanical_transition": ["与此同时", "然而", "因此", "总而言之"],
        "emotion_label": ["一种感觉", "复杂的情绪", "内心深处", "说不出的"],
    }
    for code, words in patterns.items():
        hits = [word for word in words if word in (text or "")]
        if hits:
            flags.append(f"{code}:{'、'.join(hits[:4])}")
    return flags


def _format_reference_cards(cards: list[dict[str, Any]]) -> str:
    if not cards:
        return ""
    lines = ["## 参考小说拆解卡（机制来源，不可照抄）"]
    for idx, card in enumerate(cards, 1):
        lines.extend([
            f"### 卡片 {idx}：{card.get('source')} / {card.get('dimension')}",
            f"- 方法定位：{card.get('method_label') or '参考机制'}",
            f"- 可借鉴机制：{card.get('mechanism')}",
            f"- 本轮用途：{card.get('use_as')}",
            f"- 比对重点：{card.get('method_use') or '比对结构功能、情绪推进和读者期待管理。'}",
            f"- 避免：{card.get('avoid')}",
        ])
    lines.append("使用要求：只提炼法则和节奏，不把参考小说名词、桥段或句子写进正文。")
    return "\n".join(lines)


def _mechanism_for_dimension(dim: str, text: str) -> str:
    dim_text = dim or ""
    method_map = {
        "method_opening_promise": "拆开篇承诺、目标读者、处境压力与首钩，不搬运具体设定。",
        "method_motivation_chain": "拆人物欲望、信息边界、收益、代价与行动理由。",
        "method_chapter_function": "拆章节/场次功能、节奏控制、转折位置和钩子回收。",
        "method_reader_experience": "拆读者压力、悬念延迟、释放点、爽感和章末余味。",
        "method_reference_decomposition": "拆动作、对白、物件、视线和信息释放机制。",
        "method_research_detail": "拆事实细节如何服务可信度、氛围和人物选择。",
        "method_humanization": "拆自然对白、停顿、不完整动作和非模板化表达。",
    }
    if dim_text in method_map:
        return method_map[dim_text]
    if any(word in dim_text for word in ["人物", "角色"]):
        return "拆人物欲望、阻碍、关系压力和声音差异。"
    if any(word in dim_text for word in ["节奏", "情节", "剧情"]):
        return "拆压力源、延迟、释放、转折和章末余味。"
    if any(word in dim_text for word in ["世界", "设定"]):
        return "拆规则边界、代价系统和设定如何限制人物行动。"
    if any(word in dim_text for word in ["语言", "风格"]):
        return "拆句式密度、感官比例、对白质感和信息留白。"
    return "拆结构功能、情绪推进和读者期待管理。"


def _reference_use(task: str, dim: str) -> str:
    if dim.startswith("method_"):
        if task in {"outline", "beat_sheet"}:
            return "用于对照阶段目标，校验事件骨架、章节功能、压力升级和钩子回收。"
        if task in {"prose", "expansion", "fix"}:
            return "用于对照正文是否具备动作推进、读者体验、自然表达和可信细节。"
    if task in {"outline", "beat_sheet"}:
        return "用于组织事件骨架、压力升级和钩子回收。"
    if task in {"prose", "expansion", "fix"}:
        return "用于调整场景推进、语言质感和读者体验。"
    return "用于补足结构判断和避雷检查。"


def _method_label_for_dimension(dim: str) -> str:
    return {
        "method_opening_promise": "开篇承诺",
        "method_motivation_chain": "动机链",
        "method_chapter_function": "章节功能",
        "method_reader_experience": "读者体验",
        "method_reference_decomposition": "参考拆解",
        "method_research_detail": "事实细节",
        "method_humanization": "人味表达",
    }.get(dim, "")


def _method_use_for_dimension(dim: str) -> str:
    return {
        "method_opening_promise": "比对目标读者、故事承诺、首钩与期待管理是否清楚。",
        "method_motivation_chain": "比对欲望、阻碍、收益、代价和选择是否形成链条。",
        "method_chapter_function": "比对章节/场次是否承担明确推进功能，而不是堆材料。",
        "method_reader_experience": "比对压力、悬念、释放和余味是否能被读者感知。",
        "method_reference_decomposition": "比对是否只借机制，避免复制参考小说名词、物品和桥段。",
        "method_research_detail": "比对事实细节是否服务可信度，而不是百科式堆砌。",
        "method_humanization": "比对对白、停顿、动作残缺和表达是否自然。",
    }.get(dim, "")
