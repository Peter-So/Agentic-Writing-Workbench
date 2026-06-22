"""Scene-specific prompt templates loading actual project specs.

Each writing task (outline/character/beat/prose/fix/expansion) has its own
ChatPromptTemplate that injects the corresponding spec document as system context.

Uses MessagesPlaceholder for dynamic material injection and
FewShotChatMessagePromptTemplate for review calibration.
"""

import os
import sys
from pathlib import Path
from typing import Dict, Optional

from langchain_core.prompts import (
    ChatPromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
    MessagesPlaceholder,
    FewShotChatMessagePromptTemplate,
)
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

# ============================================================
# PROJECT PATHS
# ============================================================

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import DEFAULT_NOVEL_DIR

NOVEL_DIR = DEFAULT_NOVEL_DIR
PROMPTS_DIR = NOVEL_DIR / "prompts"


def _load_spec(filename: str, max_chars: int = 6000) -> str:
    """Load a spec file, truncated to max_chars."""
    path = PROMPTS_DIR / filename
    if path.exists():
        text = path.read_text(encoding="utf-8")
        return text[:max_chars]
    return f"[规范文件 {filename} 未找到]"


def _load_project_doc(filename: str, max_chars: int = 4000) -> str:
    """Load a project-level doc."""
    path = NOVEL_DIR / filename
    if path.exists():
        text = path.read_text(encoding="utf-8")
        return text[:max_chars]
    return ""


# ============================================================
# LOADED SPECS (cached on import)
# ============================================================

WORKFLOW_SPEC = _load_spec("00-material-driven-workflow.md")
OUTLINE_SPEC = _load_spec("01-outline-spec.md")
CHARACTER_SPEC = _load_spec("02-character-spec.md")
BEAT_SHEET_SPEC = _load_spec("03-beat-sheet-spec.md")
CHAPTER_PROSE_SPEC = _load_spec("04-chapter-prose-spec.md")
EXPANSION_SPEC = _load_spec("05-expansion-spec.md")
PROSE_FIX_SPEC = _load_spec("06-prose-fix-spec.md")

# Project-level constraints
DUAL_NARRATIVE_RULES = _load_project_doc("双线叙事规则.md")
TRAVERSAL_SETTINGS = _load_project_doc("穿越设定.md")
CAMPUS_STYLE_GUIDE = _load_project_doc("校园风格写作技巧.md")


# ============================================================
# ROLE IDENTITIES (场景化身份声明)
# ============================================================

ROLE_SCREENWRITER = "你是一位大师级的传奇影视编剧，精通故事结构、人物弧光、戏剧冲突和节奏控制。你设计的每一个情节转折都能让观众屏住呼吸。"
ROLE_NOVELIST = "你是一位诺贝尔文学奖级别的优秀文学作家，精通中文小说的语言质感、五感意象、节奏留白和情感密度。你的文字有呼吸感，每一段都是画面。"
ROLE_EDITOR = "你是一位资深出版社总编辑，拥有20年审稿经验。你的审查严格、精确、有建设性，对AI痕迹零容忍。"

# ============================================================
# STYLE DIRECTIVES (风格指令)
# ============================================================

PROSE_STYLE_DIRECTIVE = """## 风格要求
- 长短句交替，节奏有呼吸感
- 每段至少一个具象五感细节（视/听/触/嗅/味）
- 留白优于解释，动作优于心理标签
- 角色对话有区分度——不同人说不同的话
- 禁用：微微/缓缓/不禁/似乎/仿佛/忍不住"""

# ============================================================
# CORE SYSTEM PROMPT (shared material-driven paradigm)
# ============================================================

MATERIAL_DRIVEN_PARADIGM = """## 核心范式
LLM不是创作者，是材料重组器。所有输出必须有材料来源。
无来源的内容 = AI凭空生成 = 删除。

## 材料来源优先级
1. 用户源文档（大纲/人物档案/设定文档）
2. 五维资料库检索结果（标注书名·锚点）
3. 经典文学/影视作品（标注作品名·场景）
4. 项目规范文档（双线叙事/穿越设定/风格指南）

## 绝对禁止
- AI否定结构（不是A是B/并非A而是B）→ 直接写出结果
- AI身体怪癖（磨牙/抠指甲/转笔/瞳孔微缩/喉结滚动）
- 叙事者翻译潜台词（"他知道她在看他"）
- 空洞情绪标签（"像一束光""一种温暖的感觉"）
- 段尾升华/总结/点明意义（"从那天起…""他后来才知道…"）
- AI高频词：微微/缓缓/不禁/似乎/仿佛/忍不住"""


# ============================================================
# SCENE-SPECIFIC PROMPTS
# ============================================================

def build_outline_prompt() -> ChatPromptTemplate:
    """大纲生成 Prompt — 注入 outline-spec + 材料."""
    return ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(
            ROLE_SCREENWRITER + "\n\n" + MATERIAL_DRIVEN_PARADIGM + "\n\n## 大纲规范\n" + OUTLINE_SPEC
        ),
        MessagesPlaceholder(variable_name="project_context", optional=True),
        HumanMessagePromptTemplate.from_template(
            """请根据以下材料重组为第{chapter_num}章大纲。

## 源文档事件
{source_events}

## 五维资料库检索（twists/scenes）
{retrieval_results}

## 人物档案出场清单
{characters}

## 时间线节点
{timeline}

按大纲规范输出，每个事件标注来源。"""
        )
    ])


def build_character_prompt() -> ChatPromptTemplate:
    """人物档案生成 Prompt — 注入 character-spec."""
    return ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(
            ROLE_SCREENWRITER + "\n\n" + MATERIAL_DRIVEN_PARADIGM + "\n\n## 人物档案规范\n" + CHARACTER_SPEC
        ),
        MessagesPlaceholder(variable_name="project_context", optional=True),
        HumanMessagePromptTemplate.from_template(
            """请根据以下材料重组为人物档案。

## 源文档角色描述
{source_character}

## 五维characters检索（同类角色行为模式）
{character_refs}

## 五维psychology检索（同类处境心理机制）
{psychology_refs}

## 经典参考角色
{classic_refs}

按人物档案规范输出，每个特质标注来源。零件重组法：从多本小说各取一个零件。"""
        )
    ])


def build_beat_sheet_prompt() -> ChatPromptTemplate:
    """章节概述(Beat Sheet) Prompt — 注入 beat-sheet-spec."""
    return ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(
            ROLE_SCREENWRITER + "\n\n" + MATERIAL_DRIVEN_PARADIGM + "\n\n## 章节概述规范\n" + BEAT_SHEET_SPEC
        ),
        HumanMessagePromptTemplate.from_template(
            """请根据以下材料重组为第{chapter_num}章的Beat Sheet。

## 大纲本章事件块
{outline_section}

## 出场角色档案
{characters}

## 五维scenes检索（同类场景展开方式）
{scene_refs}

## 五维twists检索（节奏机制）
{twist_refs}

按概述规范输出，每个beat用「压力源→延迟→释放→余味」结构。"""
        )
    ])


def build_chapter_prose_prompt() -> ChatPromptTemplate:
    """章节正文生成 Prompt — 注入完整prose-spec + 校园风格 + 双线规则."""
    # Combine prose spec with style guide
    combined_system = (
        ROLE_NOVELIST + "\n\n"
        + MATERIAL_DRIVEN_PARADIGM
        + "\n\n" + PROSE_STYLE_DIRECTIVE
        + "\n\n## 章节正文规范\n" + CHAPTER_PROSE_SPEC
        + "\n\n## 校园风格指南\n" + CAMPUS_STYLE_GUIDE[:2000]
    )
    # Add dual-narrative rules if relevant
    if DUAL_NARRATIVE_RULES:
        combined_system += "\n\n## 双线叙事规则\n" + DUAL_NARRATIVE_RULES[:1500]

    return ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(combined_system),
        MessagesPlaceholder(variable_name="material_context", optional=True),
        HumanMessagePromptTemplate.from_template(
            """请根据以下材料重组为第{chapter_num}章正文。

## 大纲本章事件（严格遵循）
{outline_section}

## 出场角色档案（对话/行为从此推导）
{characters}

## 五维资料库参考（提取机制后原创转化）
{retrieval_results}

## 时间线约束
{timeline}

## 额外约束
{constraints}

要求：4000-6000字，第三人称有限视角。
每段写完在心中确认材料来源（大纲/人物/五维/规范中的哪一条）。"""
        )
    ])


def build_expansion_prompt() -> ChatPromptTemplate:
    """扩写 Prompt — 注入 expansion-spec."""
    return ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(
            ROLE_NOVELIST + "\n\n" + MATERIAL_DRIVEN_PARADIGM + "\n\n" + PROSE_STYLE_DIRECTIVE + "\n\n## 扩写规范\n" + EXPANSION_SPEC
        ),
        HumanMessagePromptTemplate.from_template(
            """请扩写以下段落的薄弱点。

## 已有正文
{existing_text}

## 薄弱点标注
{weak_points}

## 补充材料（五维检索）
{retrieval_results}

## 角色档案
{characters}

## 大纲对照（不可偏离）
{outline_section}

按扩写规范执行：识别薄弱→检索机制→原创转化→无缝嵌入。输出完整修改后正文。"""
        )
    ])


def build_fix_prompt() -> ChatPromptTemplate:
    """修复 Prompt — 注入 prose-fix-spec."""
    return ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(
            ROLE_EDITOR + "\n\n" + MATERIAL_DRIVEN_PARADIGM + "\n\n## 修复规范\n" + PROSE_FIX_SPEC
        ),
        HumanMessagePromptTemplate.from_template(
            """请修复以下章节中的问题。

## 审查报告（问题清单）
{issues}

## 原文
{original_text}

## 替换材料（五维检索）
{retrieval_results}

## 角色档案（确保修复后人物一致）
{characters}

## 大纲对照（确保不偏离）
{outline_section}

按修复规范执行：定位→检索替换材料→提取正确机制→材料替换→融合。
输出修复后的完整正文。"""
        )
    ])


# ============================================================
# REVIEW PROMPT (with few-shot calibration)
# ============================================================

REVIEW_FEW_SHOT_EXAMPLES = [
    {
        "input": "封云不是害怕，而是一种说不清的紧张。他下意识地攥紧拳头，瞳孔微缩。",
        "output": '{"de_ai":{"score":30,"issues":["不是A而是B否定结构","下意识地攥紧拳头=AI怪癖","瞳孔微缩=AI怪癖"],"suggestions":["直接写紧张的具体表现：手心汗湿/书包带勒进肩膀"]}}'
    },
    {
        "input": "她搬到第三轮时额头出汗，有男生说帮忙，她说不用——走到封云桌前注意到旧课本，轻声说'课本都一样的'",
        "output": '{"de_ai":{"score":95,"issues":[],"suggestions":[]},"literary":{"score":92,"issues":[],"suggestions":["可补充搬课本时身体的具体感受"]}}'
    },
    {
        "input": "从那天起，封云知道了什么叫真正的友情。他微微一笑，心中涌起一股暖意。",
        "output": '{"de_ai":{"score":40,"issues":["段尾升华/点明意义","微微=AI高频词","心中涌起暖意=空洞情绪标签"],"suggestions":["删除从那天起整句，止于具体动作","用五感细节替代暖意"]}}'
    },
]

REVIEW_EXAMPLE_PROMPT = ChatPromptTemplate.from_messages([
    ("human", "审查这段文字：{input}"),
    ("ai", "{output}"),
])

REVIEW_FEW_SHOT_TEMPLATE = FewShotChatMessagePromptTemplate(
    example_prompt=REVIEW_EXAMPLE_PROMPT,
    examples=REVIEW_FEW_SHOT_EXAMPLES,
)


def build_review_prompt() -> ChatPromptTemplate:
    """五维度审查 Prompt — 含few-shot校准 + 完整prose-spec禁止规则."""
    review_system = f"""{ROLE_EDITOR}

{MATERIAL_DRIVEN_PARADIGM}

## 审查维度（每项0-100分）
1. 去AI痕迹(de_ai): 否定结构/怪癖/高频词/翻译潜台词/段尾升华
2. 文学质量(literary): 五感细节/节奏/意象密度/留白
3. 结构完整性(structure): 钩子/推进力/悬念/因果链完整
4. 人物一致性(character): 开关模型/对话区分/行为动机/档案匹配
5. 大纲符合度(outline): 事件覆盖/顺序/无多余情节

## 评分校准
- 90+: 几乎无AI痕迹，文学性强，结构完整
- 70-89: 有少量问题但整体可读
- 50-69: 问题较多，需要大幅修改
- <50: 需要重写

只输出JSON。"""

    return ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(review_system),
        REVIEW_FEW_SHOT_TEMPLATE,
        HumanMessagePromptTemplate.from_template(
            """请审查以下章节（五维度，每项0-100）。

## 章节正文
{chapter_text}

## 本章大纲
{outline}

## 相关人物设定
{characters}

## 写作约束
{constraints}

输出JSON：
```json
{{"overall_score":<int>,"de_ai":{{"score":<int>,"issues":[<str>],"suggestions":[<str>]}},"literary":{{"score":<int>,"issues":[<str>],"suggestions":[<str>]}},"structure":{{"score":<int>,"issues":[<str>],"suggestions":[<str>]}},"character":{{"score":<int>,"issues":[<str>],"suggestions":[<str>]}},"outline":{{"score":<int>,"issues":[<str>],"suggestions":[<str>]}}}}
```"""
        )
    ])


# ============================================================
# PROJECT CONTEXT BUILDER (for MessagesPlaceholder injection)
# ============================================================

def build_project_context(include_traversal: bool = False,
                          include_dual_narrative: bool = False,
                          include_settings: bool = True) -> list:
    """Build project context messages for MessagesPlaceholder injection.

    Returns list of messages with project-level constraints.
    """
    from langchain_engine.doc_loader import load_project_settings
    messages = []

    if include_settings:
        settings = load_project_settings(max_chars=2000)
        if settings:
            messages.append(SystemMessage(
                content=f"## 001项目设定（终极命题/世界观/穿越顺序）\n{settings}"
            ))

    if include_traversal and TRAVERSAL_SETTINGS:
        messages.append(SystemMessage(content=f"## 穿越设定\n{TRAVERSAL_SETTINGS[:2000]}"))

    if include_dual_narrative and DUAL_NARRATIVE_RULES:
        messages.append(SystemMessage(content=f"## 双线叙事规则\n{DUAL_NARRATIVE_RULES[:2000]}"))

    return messages


# ============================================================
# CONVENIENCE: Get all prompts as a registry
# ============================================================

PROMPT_REGISTRY = {
    "outline": build_outline_prompt,
    "character": build_character_prompt,
    "beat_sheet": build_beat_sheet_prompt,
    "chapter_prose": build_chapter_prose_prompt,
    "expansion": build_expansion_prompt,
    "fix": build_fix_prompt,
    "review": build_review_prompt,
}


def get_prompt(task_type: str) -> ChatPromptTemplate:
    """Get the appropriate prompt for a task type."""
    builder = PROMPT_REGISTRY.get(task_type)
    if builder is None:
        raise ValueError(f"Unknown task type: {task_type}. "
                         f"Available: {list(PROMPT_REGISTRY.keys())}")
    return builder()
