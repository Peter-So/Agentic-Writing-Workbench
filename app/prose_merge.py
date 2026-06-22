from __future__ import annotations

import json
import re
from typing import Any

from app.config import load_runtime_config
from app.final_text_cleaner import clean_final_draft
from app.llm_client import create_llm, resolve_text_model
from app.project_kinds import SHORT_FILM_KIND
from app.provider_answer_review import format_provider_review_for_prompt

# 三篇 provider 正文：①逐篇五维提要(打分+P分级+亮点) ②按五维取最优再缝合融合成一篇。
# 复用项目现成方法论：五维口径同 writing_model_review；P0/P1/P2 同 06-prose-fix-spec。
_DIMS = ["de_ai", "literary", "structure", "character", "outline"]
_DIM_CN = {"de_ai": "去AI痕迹", "literary": "文学质量", "structure": "结构完整性",
           "character": "人物一致性", "outline": "大纲符合度"}
_FILM_DIMS = ["cinematic", "conflict", "structure", "character", "dialogue", "shootability"]
_FILM_DIM_CN = {
    "cinematic": "电影感/画面动作",
    "conflict": "冲突与推进",
    "structure": "节拍结构",
    "character": "角色弧光",
    "dialogue": "对白潜台词",
    "shootability": "可拍摄性",
}

DIGEST_PROMPT = """你是中文小说正文审稿员。只基于材料与规范评估这一篇，不补写、不改写。

## 评估维度（每项0-100分）
- de_ai 去AI痕迹：否定结构(不是A是B)、AI身体怪癖、高频词(微微/缓缓/不禁)、叙事者翻译潜台词
- literary 文学质量：具体五感细节、节奏、意象密度
- structure 结构完整性：开头钩子、推进力、结尾压力物/余韵
- character 人物一致性：符合人物设定与声音、动机可信
- outline 大纲符合度：大纲事件覆盖、顺序、无多余情节

## 问题分级（06-prose-fix）
- P0：否定结构 / 叙事者翻译潜台词
- P1：底色缺失 / 章末钩子弱 / 大纲偏差
- P2：感官细节不足

## 本章材料（精简）
{materials}

## 这一篇正文（{provider}）
{prose}

## 只输出 JSON
```json
{{"provider":"{provider}",
  "scores":{{"de_ai":0,"literary":0,"structure":0,"character":0,"outline":0}},
  "issues":[{{"severity":"P0","quote":"原句","rule":"违反项","fix":"建议"}}],
  "highlights":[{{"dimension":"literary","quote":"原句","why":"该维度为何最优"}}],
  "reusable":["可复用的具体意象/句子"]}}
```"""

FILM_DIGEST_PROMPT = """你是电影短片开发审稿员。只基于材料与规范评估这一篇，不补写、不改写。

## 评估维度（每项0-100分）
- cinematic 电影感/画面动作：画面是否具体、动作是否可见、声音/剪辑是否有设计
- conflict 冲突与推进：主角欲望、阻碍、选择、代价是否清楚
- structure 节拍结构：开场、触发、升级、反转、结尾余味是否成立
- character 角色弧光：动机、关系、变化是否可信且可表演
- dialogue 对白潜台词：对白是否有潜台词，避免直白说明
- shootability 可拍摄性：场景、镜头、调度是否能落地拍摄

## 本项目材料（精简）
{materials}

## 这一篇答案（{provider}）
{prose}

## 只输出 JSON
```json
{{"provider":"{provider}",
  "scores":{{"cinematic":0,"conflict":0,"structure":0,"character":0,"dialogue":0,"shootability":0}},
  "issues":[{{"severity":"P0","quote":"原句","rule":"违反项","fix":"建议"}}],
  "highlights":[{{"dimension":"cinematic","quote":"原句","why":"该维度为何最优"}}],
  "reusable":["可复用的镜头/节拍/对白/设定"]}}
```"""


def _dims(project_kind: str = "", task: str = "") -> list[str]:
    return _FILM_DIMS if project_kind == SHORT_FILM_KIND else _DIMS


def _dim_cn(project_kind: str = "") -> dict[str, str]:
    return _FILM_DIM_CN if project_kind == SHORT_FILM_KIND else _DIM_CN


def _parse_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL) or re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(1) if (m.re.groups and m.lastindex) else m.group(0)
    try:
        return json.loads(text)
    except Exception:
        return None


def digest_one(provider: str, prose: str, materials_brief: str,
               model_key: str | None = None, project_kind: str = "", task: str = "prose") -> dict[str, Any]:
    """逐篇五维提要：单篇输入(1篇+精简材料)，token 可控不撑爆。"""
    cfg = load_runtime_config()
    model_key = resolve_text_model(cfg, "review", model_key)
    llm = create_llm(cfg, model_key, temperature=0.05, max_tokens=2000)
    template = FILM_DIGEST_PROMPT if project_kind == SHORT_FILM_KIND else DIGEST_PROMPT
    prompt = template.format(materials=materials_brief[:4000], provider=provider, prose=prose[:14000])
    raw = ""
    try:
        for chunk in llm.stream(prompt):
            raw += getattr(chunk, "content", "") or ""
    except Exception:
        resp = llm.invoke(prompt)
        raw = getattr(resp, "content", "") or ""
    parsed = _parse_json(raw) or {}
    scores = {d: int((parsed.get("scores") or {}).get(d, 0) or 0) for d in _dims(project_kind, task)}
    return {
        "provider": provider,
        "scores": scores,
        "issues": parsed.get("issues") or [],
        "highlights": parsed.get("highlights") or [],
        "reusable": parsed.get("reusable") or [],
        "ok": bool(parsed),
    }


def best_per_dimension(digests: list[dict[str, Any]], project_kind: str = "", task: str = "prose") -> dict[str, str]:
    """五维择优表：每个维度取得分最高的那一篇 provider（argmax）。"""
    table: dict[str, str] = {}
    for d in _dims(project_kind, task):
        best, best_score = None, -1
        for dg in digests:
            s = (dg.get("scores") or {}).get(d, 0)
            if s > best_score:
                best, best_score = dg.get("provider"), s
        if best:
            table[d] = best
    return table


# 三篇全文合计的字符上限：超过则降级为"提要驱动融合"（不塞全文，仅用提要重写）。
MERGE_FULLTEXT_LIMIT = 24000

MERGE_SYSTEM = (
    "你是正文融合编辑，按“五维取最优再缝合”产出集三家所长的本章正文。"
    "你是材料重组器，不自由发挥；遵守规范与限制。最终只输出纯正文，禁止输出来源标签、审稿说明或评分表。"
)

FILM_MERGE_SYSTEM = (
    "你是电影短片开发总编剧，负责把多家 AI 的答案评分、去重、择优并融合。"
    "你只基于项目材料和 provider 亮点重组，不自由扩写世界观；最终输出必须可拍、可改、可落盘。"
)


def _fusion_prompt(materials_brief: str, spec: str, digests: list[dict], table: dict[str, str],
                   drafts: dict[str, str], use_fulltext: bool,
                   project_kind: str = "", task: str = "prose",
                   provider_review: dict[str, Any] | None = None) -> str:
    dim_cn = _dim_cn(project_kind)
    table_lines = "\n".join(f"- {dim_cn.get(d, d)}：以「{p}」为最优" for d, p in table.items())
    digest_json = json.dumps(digests, ensure_ascii=False)[:6000]
    parts = [
        "## 本章材料（精简）", materials_brief[:3500],
        "\n## 写作规范与限制", spec[:2500] if spec else "（通用规范）",
        "\n## 三篇五维择优表（程序计算，按此分配各维度采用哪一篇的优点）", table_lines,
        "\n## 三篇逐篇提要（含五维分/问题/亮点）", digest_json,
    ]
    review_text = format_provider_review_for_prompt(provider_review or {})
    if review_text:
        parts.append("\n" + review_text)
    if use_fulltext:
        parts.append("\n## 三篇正文全文")
        for prov, text in drafts.items():
            parts.append(f"### {prov}\n{text}")
    if project_kind == SHORT_FILM_KIND:
        task_rules = {
            "logline": "输出短片概念开发稿：logline、主题、主角欲望、障碍、反转、结尾余味。",
            "character": "输出角色开发稿：角色功能、欲望、阻碍、秘密、关系、弧光、可拍摄行为。",
            "beat_sheet": "输出节拍表：每个节拍包含画面、冲突、转折、声音/道具线索。",
            "screenplay": "输出剧本正文：场景标题、动作描写、角色名、对白，避免小说式心理旁白。",
            "prose": "输出剧本正文：场景标题、动作描写、角色名、对白，避免小说式心理旁白。",
            "shot_list": "输出分镜/镜头表：镜号、景别、画面、声音、备注。",
            "fix": "输出修订后的版本，并在末尾简列关键修订点。",
        }
        parts += [
            "\n## 融合指令",
            "1. 以项目简报/节拍表为骨架，优先保留冲突清楚、画面可拍、对白有潜台词的部分。",
            "2. 各维度采用择优表指定 provider 的优点，去掉重复、空泛说明和网页噪声。",
            "3. 规避所有 P0/P1 问题，统一角色动机、影像风格和节奏。",
            "4. 不要写审稿过程，不要输出评分表，只输出融合后的最终内容。",
            f"5. {task_rules.get(task, '按电影短片项目范式输出可直接采用的内容。')}",
        ]
    else:
        parts += [
            "\n## 融合指令",
            "1. 以大纲为骨架（取 outline 维度最优篇的事件覆盖）。",
            "2. 各维度采用择优表指定篇的优点：去AI痕迹最干净篇的句法、文学最优篇的感官细节、"
            "结构最优篇的钩子与节奏、人物最优篇的声音与动机。",
            "3. 规避所有 P0/P1 问题。",
            "4. 缝合处统一为项目人物声音与风格，消除三篇文风冲突。",
            "5. 只输出可直接归档的正文，不要输出 [五维]、[源文档]、[provider]、[角色]、[技法] 等任何溯源标签。",
            "6. 输出完整本章正文，4000-6000 字。" if use_fulltext else
            "6. 仅依据提要与材料重写出完整本章正文，4000-6000 字（未提供全文，按提要还原最优内容）。",
        ]
    return "\n".join(parts)


def merge_drafts(drafts: dict[str, str], digests: list[dict[str, Any]],
                 materials_brief: str, spec: str = "", model_key: str | None = None,
                 on_token=None, project_kind: str = "", task: str = "prose",
                 provider_review: dict[str, Any] | None = None) -> dict[str, Any]:
    """步骤2：按五维择优表融合三篇为一篇。

    drafts: {provider_name: prose}; digests: digest_one 的列表。
    超长则降级为"提要驱动融合"（不塞全文）。on_token 可选回调用于流式透出。
    """
    table = best_per_dimension(digests, project_kind=project_kind, task=task)
    total = sum(len(t) for t in drafts.values())
    use_fulltext = total <= MERGE_FULLTEXT_LIMIT
    cfg = load_runtime_config()
    model_key = resolve_text_model(cfg, "writing", model_key)
    llm = create_llm(cfg, model_key, temperature=0.6, max_tokens=8000)
    # 打标记：SSE 流式只透出"融合正文"这次调用的 token（区别于逐篇提要 digest_one）。
    llm = llm.with_config({"tags": ["prose_merge"]})
    prompt = _fusion_prompt(materials_brief, spec, digests, table, drafts, use_fulltext,
                            project_kind=project_kind, task=task, provider_review=provider_review)
    system = FILM_MERGE_SYSTEM if project_kind == SHORT_FILM_KIND else MERGE_SYSTEM
    messages = [{"role": "system", "content": system}, {"role": "human", "content": prompt}]
    text = ""
    try:
        for chunk in llm.stream(messages):
            piece = getattr(chunk, "content", "") or ""
            if piece:
                text += piece
                if on_token:
                    on_token(piece)
    except Exception:
        resp = llm.invoke(messages)
        text = getattr(resp, "content", "") or ""
    text = clean_final_draft(text, task=task, project_kind=project_kind)
    return {
        "ok": bool(text.strip()),
        "model": model_key,
        "text": text.strip(),
        "best_per_dimension": table,
        "used_fulltext": use_fulltext,
    }
