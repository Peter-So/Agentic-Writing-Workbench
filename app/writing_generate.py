from __future__ import annotations

from typing import Any

from app.config import load_runtime_config
from app.llm_client import create_llm, resolve_text_model
from app.writing_task_profiles import is_novel_planning_task, novel_stage_profile


# 材料驱动生成：严格遵守 00-material-driven-workflow.md 的 Prompt 结构
# [系统指令] LLM 是材料重组器，不是创作者
# [材料区] 必须使用的材料（含 provider 答案，阶段 C 注入）
# [规则区] 必须遵守的规范（spec）
# [重组指令] 如何改造材料
# [输出格式] 每段标注材料来源
SYSTEM_INSTRUCTION = (
    "你是中文小说写作的材料重组器，不是自由创作者。"
    "你只能基于下方提供的材料进行重组改造：提取材料中的机制与模式，"
    "用本项目的人物、设定、风格重新表达。严禁脱离材料自由发挥。"
    "违反任何一条规则约束即为失败。"
)


def _novel_planning_output_rules(task: str | None) -> list[str]:
    profile = novel_stage_profile(task)
    signals = profile.get("acceptance_signals") or []
    sections = profile.get("material_sections") or []
    common = [
        f"[阶段标识] {profile.get('label') or '小说前期规划'}（{profile.get('id') or 'planning'}）",
        "[输出格式] 输出可直接归档到项目结构文件的结构稿，不要写成章节正文。",
        "不要在段落末尾标注 provider、五维、技法、源文档等来源标签。",
        "不要输出“修改建议”“优化要点”“本轮说明”“可选方案对比”，只保留用户可采纳的定稿内容。",
    ]
    if sections:
        common.append("材料边界优先围绕：" + "、".join(sections) + "。")
    if signals:
        common.append("建议包含：" + "、".join(signals) + "。")
    else:
        common.append("按用户目标输出结构清晰的小说前期规划稿。")
    return common


def _format_materials(materials: dict[str, Any], provider_answers: list[dict] | None,
                      cross_chapter: list[dict] | None = None,
                      long_term_settings: list[dict] | None = None,
                      output_recall: list[dict] | None = None,
                      wiki_items: list[dict] | None = None,
                      project_wiki_items: list[dict] | None = None) -> str:
    """把 materials + provider 答案 + 跨章节进展 + 长期设定 + 产出语义召回拼成"材料区"文本，带来源标注。"""
    parts: list[str] = []

    # LLM Wiki：人工确认后的稳定规则/项目共识，权威高于普通 RAG 片段。
    if wiki_items:
        try:
            from app.writing_wiki import format_wiki_for_prompt
            wiki_block = format_wiki_for_prompt(wiki_items)
            if wiki_block:
                parts.append("## LLM Wiki：稳定规则与项目共识\n" + wiki_block)
        except Exception:
            pass

    # 项目级动态 Wiki：项目内过程知识、当前状态、待办、材料备注和路由说明。
    if project_wiki_items:
        try:
            from app.project_wiki import format_project_wiki_for_prompt
            project_wiki_block = format_project_wiki_for_prompt(project_wiki_items)
            if project_wiki_block:
                parts.append("## 项目 Wiki：过程知识与项目状态\n" + project_wiki_block)
        except Exception:
            pass

    # 产出库语义召回：既往已确认章节/摘要/设定的语义相关片段（RAG 增量记忆）。
    for item in output_recall or []:
        meta = item.get("meta") or {}
        ch = meta.get("chapter")
        typ = meta.get("type", "产出")
        txt = (item.get("text") or "").strip()
        if txt:
            parts.append(f"### 既往产出语义召回\n[产出·第{ch}章·{typ}]\n{txt[:500]}")

    # 长期创作设定（人物卡/世界观/偏好）：跨会话固化的约束，优先级高。
    for item in long_term_settings or []:
        key = item.get("key", "")
        val = item.get("value")
        text = val if isinstance(val, str) else "；".join(f"{k}:{v}" for k, v in (val or {}).items())
        if text:
            parts.append(f"### 长期创作设定\n[设定·{key}]\n{text}")

    # 跨章节进展（连续性记忆）：放最前，提醒模型延续前情。仅章节环节会带。
    for item in cross_chapter or []:
        ch = item.get("chapter")
        rel = "相邻前章" if item.get("relation") == "adjacent" else "强关联章"
        bits = []
        if item.get("events"): bits.append("已发生：" + "；".join(item["events"][:6]))
        if item.get("resolved"): bits.append("已解决：" + "；".join(item["resolved"][:4]))
        if item.get("open_threads"): bits.append("未解决：" + "；".join(item["open_threads"][:4]))
        if item.get("character_changes"): bits.append("人物变化：" + "；".join(item["character_changes"][:4]))
        if bits:
            parts.append(f"### 跨章节进展（{rel}）\n[进展·第{ch}章]\n" + "\n".join(bits))

    outline = materials.get("chapter_outline")
    if outline:
        parts.append(f"### 本章大纲（骨架来源）\n[源文档·大纲]\n{outline}")

    outline_context = materials.get("outline_context")
    if outline_context:
        parts.append(f"### 大纲连续性对照材料\n[源文档·大纲连续性]\n{outline_context}")

    target_locations = materials.get("target_prose_locations") or []
    if isinstance(target_locations, list) and target_locations:
        lines = ["### 待改正文定位（只围绕这些行修订，不要重写整章）"]
        for idx, loc in enumerate(target_locations[:5], start=1):
            if not isinstance(loc, dict):
                continue
            excerpt = str(loc.get("excerpt") or "").strip()
            if not excerpt:
                continue
            start = loc.get("start_line") or "?"
            end = loc.get("end_line") or "?"
            lines.append(f"[正文·片段{idx}·第{start}-{end}行]\n{excerpt[:1000]}")
        if len(lines) > 1:
            parts.append("\n\n".join(lines))

    profiles = materials.get("character_profiles")
    if profiles:
        parts.append(f"### 相关人物档案（声音来源）\n[源文档·人物档案]\n{profiles}")

    constraints = materials.get("constraints")
    if constraints:
        parts.append(f"### 项目核心约束\n[源文档·立意/世界观]\n{constraints}")

    project_docs = materials.get("project_docs") or {}
    if isinstance(project_docs, dict):
        for name, text in project_docs.items():
            if name in {"brief.md", "characters.md"}:
                continue
            if (text or "").strip():
                parts.append(f"### 项目文档：{name}\n[源文档·{name}]\n{str(text)[:3000]}")

    semantic = materials.get("semantic_results") or []
    if semantic:
        lines = ["### 五维资料库·语义检索（机制来源，提取机制勿抄内容）"]
        for item in semantic[:8]:
            book = item.get("book") or item.get("novel") or "未知"
            dim = item.get("dimension") or ""
            text = (item.get("text") or item.get("content") or "")[:400]
            lines.append(f"[五维·{book}·{dim}]\n{text}")
        parts.append("\n\n".join(lines))

    five_dim = materials.get("five_dim_results") or []
    if five_dim:
        lines = ["### 五维资料库·精确检索"]
        for item in five_dim[:10]:
            book = item.get("book") or item.get("novel") or "未知"
            dim = item.get("dimension") or ""
            text = (item.get("text") or item.get("content") or "")[:300]
            lines.append(f"[五维·{book}·{dim}]\n{text}")
        parts.append("\n\n".join(lines))

    # provider 答案作为前置素材（阶段 C：创作模式+AI 同开时注入），与五维并列为一类来源
    for ans in provider_answers or []:
        name = ans.get("name") or ans.get("provider") or "provider"
        text = (ans.get("result") or "").strip()
        if text:
            parts.append(f"### 在线 AI 协同答案（交叉印证素材，去重去冲突后重组）\n[provider·{name}]\n{text[:3000]}")

    return "\n\n".join(parts) if parts else "（无材料，禁止凭空生成，应退回补充材料）"


def build_generation_prompt(
    bundle: dict[str, Any],
    provider_answers: list[dict] | None = None,
    revise_target: str = "",
    review_feedback: str = "",
) -> list[dict[str, str]]:
    """构建材料驱动生成的消息列表（system + human）。"""
    materials = bundle.get("materials") or {}
    spec = bundle.get("spec") or ""
    recompose = bundle.get("recompose_instruction") or ""
    project_kind = bundle.get("project_kind") or "novel_strong"
    user_request = (bundle.get("user_request") or bundle.get("request_text") or "").strip()
    request_analysis = bundle.get("request_analysis") or {}
    deliverable = request_analysis.get("deliverable") or ""
    answer_style = request_analysis.get("answer_style") or ""
    generator_instruction = request_analysis.get("generator_instruction") or ""
    task = bundle.get("task", "prose")

    material_block = _format_materials(
        materials, provider_answers, bundle.get("cross_chapter"), bundle.get("long_term_settings"),
        bundle.get("output_recall"), bundle.get("wiki_items"), bundle.get("project_wiki_items"),
    )
    technique_block = ""
    try:
        from app.writing_techniques import technique_context_for_task

        outline_hint = "\n".join(filter(None, [
            str(materials.get("chapter_outline") or ""),
            str(materials.get("outline_context") or ""),
        ]))
        technique_ctx = technique_context_for_task(
            query="\n".join(filter(None, [
                user_request,
                generator_instruction,
                str(request_analysis.get("reason") or ""),
            ])),
            outline=outline_hint,
            project_kind=project_kind,
            task=task,
            model_key=(bundle.get("model_preferences") or {}).get("review"),
            max_lines=6,
        )
        technique_block = technique_ctx.get("text") or ""
        if technique_ctx.get("ok"):
            bundle["technique_context"] = technique_ctx
    except Exception:
        technique_block = ""
    skill_block = ""
    if project_kind == "novel_strong":
        try:
            from app.novel_skills import load_novel_skill_text

            skill_block = load_novel_skill_text(bundle.get("novel_id"), bundle.get("task", "prose"))
        except Exception:
            skill_block = ""

    human_sections = [
        "[用户具体要求] 本轮必须直接回答用户的问题：",
        user_request or "（用户未提供额外要求，按任务类型处理）",
        "",
        "[材料区] 以下是你必须使用的材料：",
        material_block,
        "",
        "[技能范式] 以下技能只作为创作方法和检查点，不得照抄技能文本：",
        skill_block or "（无额外技能卡。）",
        "",
        "[写作技巧知识库] 以下技法只作为表达法则和检查点，不得写成剧情片段复述：",
        technique_block or "（未匹配到额外技法，按任务规范执行。）",
        "",
        "[规则区] 以下是你必须遵守的规范（违反任一条即失败）：",
        spec or "（未加载规范，按通用中文小说写作规范执行）",
        "",
        "[任务行为] 优先服从用户具体要求与上游请求理解结果；不要把材料装配误当成最终回答。",
    ]
    if generator_instruction:
        human_sections += [
            "",
            "[请求理解结果] 本轮生成节点按以下意图执行：",
            generator_instruction,
        ]
    human_sections += [
        "",
        "[重组指令] 将上述材料按以下方式重组：",
        recompose or "提取机制不抄内容；用项目人物/设定/风格重新表达；每段标注材料来源。",
    ]
    if revise_target:
        human_sections += [
            "",
            "[待修订正文] 以下是需要改造的已有正文：",
            revise_target,
        ]
    if review_feedback:
        human_sections += [
            "",
            "[审查反馈] 上一轮未通过，请针对以下问题用材料补充重组（不要简单修补）：",
            review_feedback,
        ]
    if deliverable == "audit_report" or answer_style == "structured_report":
        human_sections += [
            "",
            "[输出格式] 输出结构化检查报告，至少包含：结论、主要事件完整性、章节衔接、因果链、人物设定一致性、需要修正的问题、建议调整。",
            "必须引用材料中的第一章/第二章/目标章内容作为依据；不要生成新的大纲正文。",
        ]
    elif project_kind == "short_film":
        human_sections += [
            "",
            "[输出格式] 按电影短片脚本/开发文档格式输出；如写剧本，使用场景标题、动作描写、角色名、对白。",
            "少写小说式内心旁白，多写可拍摄的动作、画面和声音。",
        ]
    elif is_novel_planning_task(project_kind, task):
        human_sections += [
            "",
            *_novel_planning_output_rules(task),
        ]
    elif project_kind == "novel_strong" and task in {"expansion", "fix"}:
        human_sections += [
            "",
            "[输出格式] 只输出可替换/可插入的修订正文，不要解释过程。",
            "必须围绕待改正文定位处理，不要重写整章；不要在段尾标注 provider、五维、角色、技法或源文档信息。",
        ]
    elif project_kind == "generic":
        human_sections += [
            "",
            "[输出格式] 按用户任务输出结构清晰、可编辑的结果；不要强制套小说章节格式。",
        ]
    else:
        human_sections += [
            "",
            "[输出格式] 直接输出正文。每段结尾用方括号标注材料来源，",
            "如 [五维·书名·维度] / [源文档·大纲] / [provider·千问] / [技法·压力源→延迟→释放→余味]。",
            "无材料来源的段落一律不要写。",
        ]
    if task in {"outline", "character", "screenplay", "shot_list", "beat_sheet", "logline", "setting", "world"}:
        human_sections += [
            "",
            "[归档边界] 输出必须是可直接写入项目结构文件的定稿内容。",
            "不要把“修改建议”“优化要点”“本轮说明”“优化版大纲”等解释性内容混入定稿正文。",
            "如确需提示风险，只能放在模型审查/影响范围中，不放入本次生成稿正文。",
        ]
    if task == "outline":
        human_sections += [
            "大纲任务只输出目标大纲段落本身；如果是指定章节大纲，保留章节标题和该章大纲内容即可。",
        ]

    return [
        {"role": "system", "content": SYSTEM_INSTRUCTION},
        {"role": "human", "content": "\n".join(human_sections)},
    ]


def generate_prose(
    bundle: dict[str, Any],
    model_key: str | None = None,
    provider_answers: list[dict] | None = None,
    revise_target: str = "",
    review_feedback: str = "",
    temperature: float = 0.7,
    max_tokens: int = 4000,
) -> dict[str, Any]:
    """调用生成模型（默认 claude）做材料驱动重组，返回正文与所用模型。

    用流式调用：长文本生成时让网关持续收到 token，避免 origin 120s 读超时（Cloudflare 524）。
    """
    cfg = load_runtime_config()
    model_key = resolve_text_model(cfg, "writing", model_key)
    messages = build_generation_prompt(bundle, provider_answers, revise_target, review_feedback)
    llm = create_llm(cfg, model_key, temperature=temperature, max_tokens=max_tokens)
    # 打标记：正文生成的 token 供 SSE 流式透出（与逐篇提要/审查区分）。
    llm = llm.with_config({"tags": ["prose_merge"]})
    text = ""
    try:
        for chunk in llm.stream(messages):
            text += getattr(chunk, "content", "") or ""
    except Exception:
        # 流式不可用时回退一次性调用
        resp = llm.invoke(messages)
        text = getattr(resp, "content", "") or ""
    spec = cfg.models.get(model_key)
    return {
        "ok": bool(text.strip()),
        "model": model_key,
        "model_name": spec.name if spec else model_key,
        "text": text.strip(),
    }
