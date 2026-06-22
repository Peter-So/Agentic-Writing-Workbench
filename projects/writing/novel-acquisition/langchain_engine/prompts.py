"""LangChain-native Prompt Templates for writing workflow.

Structured prompts using ChatPromptTemplate, FewShotPromptTemplate,
MessagesPlaceholder for proper role-based message assembly.
"""

from langchain_core.prompts import (
    ChatPromptTemplate,
    FewShotChatMessagePromptTemplate,
    HumanMessagePromptTemplate,
    SystemMessagePromptTemplate,
    MessagesPlaceholder,
)
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage


# ============================================================
# SYSTEM PROMPTS (role definitions)
# ============================================================

REVIEWER_SYSTEM = """你是一位资深出版社总编辑，拥有20年审稿经验。你的审查严格、精确、有建设性，对AI痕迹零容忍。
你的审查标准：
- 去AI痕迹：零容忍解释性否定结构、AI身体怪癖、叙事者翻译潜台词
- 文学质量：要求具体五感细节、节奏变化、意象密度
- 结构：起承转合完整、有推进力、有悬念
- 人物：行为动机可信、对话区分度高、符合设定
- 大纲：关键事件全覆盖、无多余情节

你只输出JSON格式的审查结果。"""

MATERIAL_ASSEMBLER_SYSTEM = """你是一位小说创作助手，负责从原始材料中提取和组织写作素材。
你的原则：
- 只使用提供的源材料，不凭空生成
- 提取时保持原文语义，不加解释
- 按维度分类：场景细节、心理描写、人物特征、伏笔设计、智力博弈
- 优先选择具体的、有画面感的、有情感冲击的段落"""

CHAPTER_WRITER_SYSTEM = """你是一位诺贝尔文学奖级别的优秀文学作家，精通中文小说的语言质感、五感意象、节奏留白和情感密度。你的文字有呼吸感，每一段都是画面。

写作铁律：
1. 禁止AI否定结构(不是A是B/并非A而是B) — 直接写出结果
2. 禁止AI身体怪癖(磨牙/抠指甲/转笔/瞳孔微缩/喉结滚动)
3. 封云默认状态=开朗好奇，仅在被注目/被嘲笑/被观察吃东西/正式场合时沉默
4. 叙述中用"母亲/父亲"，对话中可用口语称谓
5. 每段必须有一个具象画面(五感细节)
6. 长短句交替，留白和节奏感
7. 不用"微微/缓缓/不禁/似乎/仿佛"等AI高频词
8. 角色对话要有区分度——不同人说不同的话
9. 留白优于解释，动作优于心理标签"""


# ============================================================
# REVIEW PROMPT (ChatPromptTemplate + structured output)
# ============================================================

REVIEW_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(REVIEWER_SYSTEM),
    MessagesPlaceholder(variable_name="few_shot_examples", optional=True),
    HumanMessagePromptTemplate.from_template(
        """请对以下章节进行五维度审查（每项0-100分）。

## 章节正文
{chapter_text}

## 本章大纲
{outline}

## 相关人物设定
{characters}

## 写作约束
{constraints}

输出严格JSON格式：
```json
{{
  "overall_score": <int>,
  "de_ai": {{"score": <int>, "issues": [<str>], "suggestions": [<str>]}},
  "literary": {{"score": <int>, "issues": [<str>], "suggestions": [<str>]}},
  "structure": {{"score": <int>, "issues": [<str>], "suggestions": [<str>]}},
  "character": {{"score": <int>, "issues": [<str>], "suggestions": [<str>]}},
  "outline": {{"score": <int>, "issues": [<str>], "suggestions": [<str>]}}
}}
```""")
])


# ============================================================
# FEW-SHOT EXAMPLES for review (teaches scoring calibration)
# ============================================================

REVIEW_FEW_SHOT_EXAMPLES = [
    {
        "input": "封云不是害怕，而是一种说不清的紧张。他下意识地攥紧拳头，瞳孔微缩。",
        "output": '{"de_ai": {"score": 30, "issues": ["不是A而是B否定结构", "下意识地攥紧拳头=AI怪癖", "瞳孔微缩=AI怪癖"], "suggestions": ["直接写紧张的具体表现：手心出汗/书包带勒进肩膀"]}}'
    },
    {
        "input": "封云把塑料袋里的课本抽出来码在桌角，用手肘压住袋子不让它发出声响。旁边男生正翻物理书，他凑过去瞄了眼目录。",
        "output": '{"de_ai": {"score": 95, "issues": [], "suggestions": []}, "literary": {"score": 90, "issues": [], "suggestions": ["可补充课本的具体细节(新书油墨味)"]}}'
    },
]

# Build FewShotChatMessagePromptTemplate
REVIEW_EXAMPLE_PROMPT = ChatPromptTemplate.from_messages([
    ("human", "审查这段文字：{input}"),
    ("ai", "{output}"),
])

REVIEW_FEW_SHOT = FewShotChatMessagePromptTemplate(
    example_prompt=REVIEW_EXAMPLE_PROMPT,
    examples=REVIEW_FEW_SHOT_EXAMPLES,
)


# ============================================================
# MATERIAL ASSEMBLY PROMPT
# ============================================================

MATERIAL_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(MATERIAL_ASSEMBLER_SYSTEM),
    HumanMessagePromptTemplate.from_template(
        """从以下五维资料库检索结果中，为第{chapter_num}章选取最相关的参考素材。

## 本章大纲
{outline}

## 本章涉及角色
{characters}

## 五维资料库检索结果
{retrieval_results}

请按以下维度组织输出：
1. 场景参考（可借鉴的环境/氛围描写）
2. 心理参考（类似处境的内心活动）
3. 人物参考（相似角色的特征/动作/对话）
4. 技法参考（可借鉴的叙事技法/结构手法）

每条标注来源（书名·锚点），并说明如何转化使用（不是抄袭模仿，是原创转化）。""")
])


# ============================================================
# CHAPTER WRITING PROMPT (with full context injection)
# ============================================================

CHAPTER_WRITING_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(CHAPTER_WRITER_SYSTEM),
    MessagesPlaceholder(variable_name="material_context", optional=True),
    HumanMessagePromptTemplate.from_template(
        """请根据以下材料写作第{chapter_num}章。

## 大纲要求
{outline}

## 涉及角色设定
{characters}

## 参考素材（仅供转化使用，禁止照搬）
{references}

## 额外约束
{constraints}

要求：
- 严格覆盖大纲所有事件点
- 4000-6000字
- 第三人称有限视角
- 开头有钩子，结尾留悬念
- 每个场景必须推动情节或揭示人物""")
])


# ============================================================
# FIX PROMPT (for auto-fix loop)
# ============================================================

FIX_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "你是一位资深出版社总编辑，拥有20年审稿经验，对AI痕迹零容忍。"
        "只修改指出的问题，保持其他部分不变。"
        "修改时直接写出结果，不加解释。"
    ),
    HumanMessagePromptTemplate.from_template(
        """请修复以下章节中的问题。

## 原文
{original_text}

## 需要修复的问题
{issues}

## 修复规则
- "不是A而是B"结构 → 删除整个否定，直接写出B的具体表现
- AI身体怪癖 → 替换为具体的环境细节或动作
- 叙事称谓 → 叙述中"他妈/他爸"改为"母亲/父亲"
- 高频词 → 替换为同义词或删除

输出修复后的完整正文，不要任何解释。""")
])
