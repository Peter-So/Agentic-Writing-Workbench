from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from app.novel_context import WRITING_ROOT


TECHNIQUE_KB_PATH = WRITING_ROOT / "data" / "writing-technique-knowledge.json"


SHORT_FILM_TECHNIQUE_LAWS = {
    "logline": [
        "主角欲望法：一句话里必须同时出现主角、欲望、阻碍和代价，不用抽象主题替代行动。",
        "反转余味法：短片概念要保留一个可拍的认知转折，让结尾改变观众对开场信息的理解。",
    ],
    "character": [
        "可表演欲望法：角色设定必须落到可见动作、选择习惯、说话方式和逃避方式上。",
        "关系压力法：角色关系不靠说明成立，要让每场戏里至少有一方在索取、隐瞒、拒绝或补偿。",
    ],
    "beat_sheet": [
        "节拍升级法：每个 beat 都要改变信息、关系或风险，不能只是重复同一状态。",
        "声音道具线索法：用声音、道具或空间变化串联节拍，让短片在低成本场景里仍有推进感。",
    ],
    "screenplay": [
        "可见动作法：剧本正文优先写动作、停顿、距离、视线和声音，不用小说式心理旁白解释人物。",
        "潜台词对白法：对白表面说一件事，底下压另一件事；每句对白至少推进信息、关系或冲突之一。",
    ],
    "shot_list": [
        "镜头功能法：每个镜头必须承担信息、情绪、转折或节奏功能，避免只描述漂亮画面。",
        "景别推进法：景别变化要服务信息释放和人物压力，从空间关系逐步压到动作或表情细节。",
    ],
    "fix": [
        "问题定向修订法：先明确要修复的是信息、动机、节奏、对白还是可拍性，再只改相关段落。",
        "删繁留动作法：修订短片时优先删除解释性台词，把信息转成动作、道具、停顿或声音。",
    ],
}


@lru_cache(maxsize=1)
def load_technique_knowledge() -> dict[str, Any]:
    try:
        data = json.loads(TECHNIQUE_KB_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"taxonomy": [], "techniques": []}
    return data if isinstance(data, dict) else {"taxonomy": [], "techniques": []}


def technique_context_for_task(
    *,
    query: str = "",
    outline: str = "",
    project_kind: str = "",
    task: str = "",
    model_key: str | None = None,
    max_lines: int = 6,
) -> dict[str, Any]:
    """Build a reusable technique context for generation, merge, review and memory.

    The returned text contains abstract craft laws only. It must not include
    reference excerpts, project-only nouns, or plot fragments as "techniques".
    """
    task_key = str(task or "prose")
    kind = str(project_kind or "")
    base_query = "\n".join(part for part in [query, outline] if part).strip()
    if kind == "short_film":
        lines = _short_film_laws(task_key, base_query, limit=max_lines)
        mode = "short_film_overlay"
    elif kind == "generic":
        lines = recall_technique_laws(base_query or task_key, limit=min(max_lines, 4))
        if not lines:
            lines = [
                "表达方式定位法：先判断本段承担记叙、描写、抒情、议论还是说明功能，再决定组织顺序。",
                "层层深入法：随想整理先保留核心感受，再补触发原因、具体例子和可继续扩展的问题。",
                "留白边界法：灵感类内容可以保留开放问题，但要标明哪些是确定想法、哪些仍待探索。",
            ][:max_lines]
        mode = "generic_recall"
    else:
        lines = match_techniques_for_beats(
            outline or query,
            query=query,
            limit_beats=max_lines,
            techniques_per_beat=2,
            model_key=model_key,
        )
        mode = "beat_match"
    lines = _clean_law_lines(lines, max_lines=max_lines)
    text = format_technique_context(lines, project_kind=kind, task=task_key)
    return {
        "ok": bool(lines),
        "project_kind": kind,
        "task": task_key,
        "mode": mode,
        "lines": lines,
        "text": text,
    }


def format_technique_context(lines: list[str], *, project_kind: str = "", task: str = "") -> str:
    if not lines:
        return ""
    heading = "## 写作技巧知识库：本轮技法法则"
    if project_kind == "short_film":
        heading = "## 影视写作技法知识库：本轮技法法则"
    elif project_kind == "generic":
        heading = "## 表达技巧知识库：本轮组织法则"
    body = "\n".join(f"- {line}" for line in lines[:8])
    guard = (
        "使用要求：只把这些内容当作表达法则；不要在正文中解释术语，"
        "不要照搬任何参考片段、人物、物件或句子。"
    )
    return f"{heading}\n{body}\n{guard}"


def recall_technique_laws(query: str, *, limit: int = 5) -> list[str]:
    """Recall abstract writing technique laws for prompt construction.

    This returns provider-safe expression laws, not source excerpts. It is used
    as a knowledge-base layer below project-specific outline/reference hints.
    """
    kb = load_technique_knowledge()
    terms = set(_terms(query))
    scored: list[tuple[float, dict[str, Any]]] = []
    for item in kb.get("techniques") or []:
        if not isinstance(item, dict):
            continue
        haystack = " ".join([
            str(item.get("name") or ""),
            " ".join(str(v) for v in item.get("aliases") or []),
            " ".join(str(v) for v in item.get("use_when") or []),
            str(item.get("definition") or ""),
        ])
        item_terms = set(_terms(haystack))
        overlap = len(terms & item_terms)
        direct = 0
        for word in [item.get("name"), *(item.get("aliases") or [])]:
            if word and str(word) in (query or ""):
                direct += 3
        category_bonus = _category_bonus(item, query)
        score = overlap + direct + category_bonus
        if score > 0:
            scored.append((float(score), item))
    scored.sort(key=lambda pair: (pair[0], str(pair[1].get("id") or "")), reverse=True)
    laws: list[str] = []
    seen: set[str] = set()
    for _score, item in scored:
        name = str(item.get("name") or "").strip()
        law = str(item.get("prompt_law") or "").strip()
        if not name or not law:
            continue
        line = f"{name}：{law}"
        if line in seen:
            continue
        seen.add(line)
        laws.append(line)
        if len(laws) >= limit:
            break
    return laws


def match_techniques_for_beats(
    outline: str,
    *,
    query: str = "",
    limit_beats: int = 6,
    techniques_per_beat: int = 2,
    model_key: str | None = None,
) -> list[str]:
    """Match chapter beats to technique laws from the knowledge base.

    Output intentionally contains only beat ordinals and knowledge-base
    technique laws. It must not leak project nouns, objects, actions, source
    snippets, or reference novel labels to external providers.
    """
    beats = _extract_beats(outline)
    if not beats and query:
        beats = [query]
    matched: list[list[str]] = []
    for beat in beats[:limit_beats]:
        ids = _rule_match_technique_ids(beat)
        if len(ids) < techniques_per_beat:
            ids.extend(_ids_from_recall(beat, limit=techniques_per_beat + 2))
        matched.append(_dedupe(ids)[:techniques_per_beat])

    if _match_quality(matched, techniques_per_beat) < 0.65:
        llm_matched = _llm_match_technique_ids(beats[:limit_beats], model_key=model_key)
        if llm_matched:
            matched = [
                _dedupe([*(llm_matched[idx] if idx < len(llm_matched) else []), *ids])[:techniques_per_beat]
                for idx, ids in enumerate(matched)
            ]

    lines: list[str] = []
    for idx, ids in enumerate(matched, 1):
        laws = [_law_for_id(tid) for tid in ids if _law_for_id(tid)]
        if not laws:
            continue
        lines.append(f"第{idx}个 beat：" + "；".join(laws[:techniques_per_beat]))
    if not lines:
        fallback = recall_technique_laws(query or outline, limit=5)
        lines = [f"通用技法：{line}" for line in fallback]
    return lines[:limit_beats]


def taxonomy_summary() -> dict[str, Any]:
    kb = load_technique_knowledge()
    taxonomy = [item for item in kb.get("taxonomy") or [] if isinstance(item, dict)]
    techniques = [item for item in kb.get("techniques") or [] if isinstance(item, dict)]
    return {
        "taxonomy_count": len(taxonomy),
        "technique_count": len(techniques),
        "term_count": sum(len(item.get("terms") or []) for item in taxonomy),
        "categories": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "term_count": len(item.get("terms") or []),
            }
            for item in taxonomy
        ],
    }


def technique_names() -> list[str]:
    return [
        str(item.get("name") or "")
        for item in load_technique_knowledge().get("techniques") or []
        if isinstance(item, dict) and item.get("name")
    ]


def _short_film_laws(task: str, query: str, *, limit: int) -> list[str]:
    laws: list[str] = []
    laws.extend(SHORT_FILM_TECHNIQUE_LAWS.get(task) or [])
    if task in {"prose", "draft"}:
        laws.extend(SHORT_FILM_TECHNIQUE_LAWS["screenplay"])
    recall_query = " ".join([
        query,
        "短片 剧本 画面 动作 声音 道具 节拍 冲突 潜台词 反转 可拍摄",
    ]).strip()
    laws.extend(recall_technique_laws(recall_query, limit=limit))
    return _dedupe(laws)[:limit]


def _clean_law_lines(lines: list[str], *, max_lines: int) -> list[str]:
    cleaned: list[str] = []
    for line in lines or []:
        text = re.sub(r"\s+", " ", str(line or "")).strip()
        text = re.sub(r"\[技法·[^\]]+\]", "", text)
        if not text:
            continue
        # Keep the line abstract and bounded for prompt injection.
        cleaned.append(text[:220])
        if len(cleaned) >= max_lines:
            break
    return _dedupe(cleaned)


def _terms(text: str) -> list[str]:
    stop = {"这个", "一种", "使用", "人物", "场景", "情节", "表达", "写法", "技法", "法则"}
    raw = re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,8}", text or "")
    return [item.lower() for item in raw if item not in stop]


def _extract_beats(outline: str) -> list[str]:
    text = outline or ""
    match = re.search(r"###\s*主要事件[^\n]*\n(?P<body>.*?)(?=\n---|\n###\s|\Z)", text, re.DOTALL)
    body = match.group("body") if match else text
    beats: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if re.match(r"^\s*(?:\d+|[一二三四五六七八九十]+)[.、．]\s*", line):
            if current:
                beats.append("\n".join(current).strip())
            current = [line]
        elif current and line.strip():
            current.append(line)
    if current:
        beats.append("\n".join(current).strip())
    if beats:
        return beats
    return [line.strip() for line in body.splitlines() if line.strip()][:6]


def _rule_match_technique_ids(text: str) -> list[str]:
    rules: list[tuple[str, list[str]]] = [
        (r"电话|沉默|忙音|母亲|父亲|家里|亲情|挂断", ["iceberg", "psychological_struggle", "line_object"]),
        (r"想起|回忆|春节|奖状|父亲|母亲|童年|倒叙", ["iceberg", "emotion_scene_blend", "echo"]),
        (r"初次|搭话|开口|问|说|翻|指出|直接|帮扶|分组", ["voice_tone", "body_exaggeration", "irony_humor"]),
        (r"班会|评议|投票|质疑|主持|黑板|集体|同学|公开|审视", ["point_surface", "baimiao", "psychological_struggle"]),
        (r"名单|申请|宣誓|组织|身份|徽|名字|写下|正式", ["line_object", "echo", "foreshadowing"]),
        (r"钩子|章末|未完成|后续|第一次|出现", ["suspense", "foreshadowing", "echo"]),
        (r"紧张|释然|隐痛|暖意|压力|停顿|等待", ["dynamic_static", "psychological_struggle", "baimiao"]),
        (r"贫困|重点班|距离|差异|公正|质疑", ["contrast", "point_surface", "baimiao"]),
    ]
    ids: list[str] = []
    for pattern, candidates in rules:
        if re.search(pattern, text):
            ids.extend(candidates)
    return _dedupe(ids)


def _ids_from_recall(text: str, *, limit: int) -> list[str]:
    laws = recall_technique_laws(text, limit=limit)
    names = {str(item.get("name") or ""): str(item.get("id") or "") for item in _techniques()}
    ids: list[str] = []
    for line in laws:
        name = line.split("：", 1)[0].strip()
        if names.get(name):
            ids.append(names[name])
    return _dedupe(ids)


def _llm_match_technique_ids(beats: list[str], *, model_key: str | None = None) -> list[list[str]]:
    if not beats:
        return []
    try:
        from app.config import load_runtime_config
        from app.llm_client import create_llm, resolve_text_model

        cfg = load_runtime_config()
        selected = resolve_text_model(cfg, "review", model_key)
        llm = create_llm(cfg, selected, temperature=0, max_tokens=900, timeout=45, max_retries=1)
        allowed = [
            {"id": str(item.get("id") or ""), "name": str(item.get("name") or ""), "use_when": item.get("use_when") or []}
            for item in _techniques()
        ]
        prompt = (
            "你是写作技法匹配器。请为每个 beat 选择最合适的 1-2 个技法 id。\n"
            "只能从 allowed_techniques 中选择 id，不要输出剧情内容、人物名、物件名、动作名或解释。\n"
            "只输出 JSON 数组，格式：[[\"technique_id\"],[\"technique_id\",\"technique_id\"]]。\n\n"
            f"allowed_techniques={json.dumps(allowed, ensure_ascii=False)}\n\n"
            f"beats={json.dumps(beats, ensure_ascii=False)}"
        )
        raw = getattr(llm.invoke(prompt), "content", "") or ""
        data = _parse_json_array(raw)
        allowed_ids = {item["id"] for item in allowed if item["id"]}
        out: list[list[str]] = []
        for row in data if isinstance(data, list) else []:
            ids = [str(item) for item in row if str(item) in allowed_ids] if isinstance(row, list) else []
            out.append(_dedupe(ids)[:2])
        return out
    except Exception:
        return []


def _parse_json_array(text: str) -> Any:
    raw = (text or "").strip()
    match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL) or re.search(r"\[.*\]", raw, re.DOTALL)
    if match:
        raw = match.group(1) if match.lastindex else match.group(0)
    try:
        return json.loads(raw)
    except Exception:
        return []


def _match_quality(matched: list[list[str]], expected: int) -> float:
    if not matched:
        return 0.0
    got = sum(min(len(row), expected) for row in matched)
    return got / max(1, len(matched) * expected)


def _law_for_id(technique_id: str) -> str:
    item = _technique_by_id().get(technique_id)
    if not item:
        return ""
    name = str(item.get("name") or "").strip()
    law = str(item.get("prompt_law") or "").strip()
    return f"{name}｜{law}" if name and law else ""


@lru_cache(maxsize=1)
def _technique_by_id() -> dict[str, dict[str, Any]]:
    return {str(item.get("id") or ""): item for item in _techniques() if item.get("id")}


def _techniques() -> list[dict[str, Any]]:
    return [item for item in load_technique_knowledge().get("techniques") or [] if isinstance(item, dict)]


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _category_bonus(item: dict[str, Any], query: str) -> int:
    category = str(item.get("category") or "")
    text = query or ""
    if category == "structure" and any(word in text for word in ["伏笔", "照应", "线索", "章末", "钩子", "铺垫"]):
        return 2
    if category == "rhetoric" and any(word in text for word in ["语气", "声调", "对白", "比喻", "讽刺", "幽默", "夸张"]):
        return 2
    if category == "artistic_expression" and any(word in text for word in ["白描", "细描", "对比", "衬托", "环境", "物件"]):
        return 2
    if category == "narrative" and any(word in text for word in ["倒叙", "插叙", "视角", "留白", "冰山"]):
        return 2
    if category == "screenwriting" and any(
        word in text for word in ["短片", "剧本", "镜头", "画面", "声音", "道具", "节拍", "潜台词", "可拍", "分镜"]
    ):
        return 3
    return 0
