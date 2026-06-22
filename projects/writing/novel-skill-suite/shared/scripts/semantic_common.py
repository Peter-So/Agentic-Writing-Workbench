from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from common import dump_json, dump_jsonl, now_iso, normalize_whitespace

SENTENCE_RE = re.compile(r"[^。！？!?；;\n]{8,220}[。！？!?；;]?")
SPEAKER_RE = re.compile(r"([\u4e00-\u9fff]{2,3})(?:冷冷|淡淡|低声|大声|沉声|笑|苦笑|摇头|点头)?(?:说道|说|道|问道|问|答道|答|喝道|叫道|喊道)")
NAME_MARKER_RE = re.compile(r"(?:名叫|叫做|叫|名为|姓|唤作|称作)([\u4e00-\u9fff]{2,4})")
CHARACTER_CONTEXT_RE = re.compile(r"([\u4e00-\u9fff]{2,3})(?:脸色|心中|眉头|眼中|身形|微微|冷冷|淡淡|一笑|苦笑|点头|摇头|沉默|出手|转身|抬头|低头)")
LOCATION_RE = re.compile(r"(?:在|到|入|进|回|离开|来到|赶到)([\u4e00-\u9fff]{2,12}(?:山|城|门|殿|府|院|楼|阁|寺|庙|洞|谷|峰|河|湖|海|村|镇|街|房|屋|厅|营|寨|墓|船|岛|林|野|台|桥|井|关|宫|界|地))")

EVENT_WORDS = (
    "发现", "决定", "遭遇", "出手", "交手", "杀", "逃", "追", "救", "死", "败",
    "胜", "离开", "来到", "进入", "回到", "得到", "失去", "揭开", "暴露", "背叛",
    "答应", "拒绝", "威胁", "逼迫", "修炼", "突破", "调查", "怀疑", "埋伏", "袭击",
)
SYSTEM_WORDS = (
    "修炼", "境界", "功法", "法宝", "灵气", "灵石", "丹药", "阵法", "门派", "宗门",
    "帮派", "官府", "军队", "江湖", "朝廷", "警察", "案件", "线索", "墓", "风水",
    "机关", "鬼", "神", "妖", "仙", "魔", "轮回", "任务", "规则", "组织", "职位",
)
STYLE_WORDS = (
    "忽然", "突然", "只是", "然而", "可是", "但", "却", "原来", "没想到", "片刻",
    "沉默", "冷笑", "苦笑", "伏笔", "秘密", "真相", "疑惑", "悬念",
)
CHARACTER_STOPWORDS = {
    "他们", "我们", "你们", "自己", "众人", "这个", "那个", "时候", "什么", "一声",
    "一下", "心中", "眼前", "脸色", "对方", "此人", "老人", "少年", "女子", "男人",
    "孩子", "大汉", "姑娘", "先生", "师父", "弟子", "掌柜", "和尚", "道人", "将军",
    "警察", "地方", "东西", "事情", "不是", "没有", "已经", "知道", "看见", "听见",
}
BAD_NAME_CHARS = set("我你他她它咱俺谁这那其某")
BAD_NAME_SUFFIXES = ("又", "也", "就", "便", "才", "已", "都", "还", "仍", "正", "对", "向", "和", "与", "把", "被", "给", "在", "了", "的", "不", "一")
BAD_NAME_PREFIXES = ("我", "你", "他", "她", "它", "这", "那", "其", "某", "谁")
BAD_NAME_SUBSTRINGS = (
    "如此", "二话", "化为", "起来", "要知", "不要", "更不", "看起", "近于", "却不",
    "终前", "留下", "以为", "未尝", "断然", "反问", "阁下", "多以", "凡有", "字一",
    "开始", "突然", "忽然", "已经", "正在", "不能", "没有", "还是", "只是", "可是",
    "但是", "因为", "所以", "如果", "虽然", "不知", "知道", "听见", "看见", "觉得",
    "说道", "问道", "答道", "喝道",
)
BAD_BIGRAM_ENDINGS = ("起", "知", "要", "不", "一", "道", "说", "问", "看", "听", "来", "去", "了", "过", "出")


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def split_sentences(text: str) -> list[str]:
    return [normalize_whitespace(m.group(0)) for m in SENTENCE_RE.finditer(text)]


def evidence_for(chapter: dict[str, object], text: str, sentence: str) -> dict[str, object]:
    local_start = text.find(sentence.rstrip("。！？!?；;"))
    if local_start < 0:
        local_start = 0
    local_end = min(len(text), local_start + len(sentence))
    base_start = int(chapter.get("start_char", 0))
    return {
        "novel_id": str(chapter["novel_id"]),
        "file_path": str(chapter["file_path"]),
        "chapter_id": str(chapter["chapter_id"]),
        "start_char": base_start + local_start,
        "end_char": base_start + local_end,
        "excerpt": sentence[:160],
    }


def score_sentence(sentence: str) -> int:
    score = 0
    score += sum(3 for word in EVENT_WORDS if word in sentence)
    score += sum(2 for word in SYSTEM_WORDS if word in sentence)
    score += sum(1 for word in STYLE_WORDS if word in sentence)
    score += min(len(sentence) // 30, 4)
    return score


def pick_summary_sentences(sentences: list[str], limit: int = 3) -> list[str]:
    if not sentences:
        return []
    ranked = sorted(enumerate(sentences), key=lambda item: (score_sentence(item[1]), -item[0]), reverse=True)
    chosen_indexes = sorted(index for index, _ in ranked[:limit])
    return [sentences[index] for index in chosen_indexes]


def pick_event_sentences(sentences: list[str], limit: int = 6) -> list[str]:
    events = [s for s in sentences if any(word in s for word in EVENT_WORDS)]
    if not events:
        events = sorted(sentences, key=score_sentence, reverse=True)[:limit]
    return events[:limit]


def valid_name(name: str) -> bool:
    if name in CHARACTER_STOPWORDS:
        return False
    if len(name) < 2 or len(name) > 4:
        return False
    if any(name.startswith(prefix) for prefix in BAD_NAME_PREFIXES):
        return False
    if any(name.endswith(suffix) for suffix in BAD_NAME_SUFFIXES):
        return False
    if any(fragment in name for fragment in BAD_NAME_SUBSTRINGS):
        return False
    if len(name) == 2 and name.endswith(BAD_BIGRAM_ENDINGS):
        return False
    if any(ch in BAD_NAME_CHARS for ch in name):
        return False
    if any(ch.isdigit() for ch in name):
        return False
    if re.search(r"[章节卷部篇]", name):
        return False
    if re.search(r"(?:于是|因为|所以|但是|只是|如果|已经|正在|开始|突然|忽然)", name):
        return False
    return True


def extract_character_names(text: str) -> Counter[str]:
    names: Counter[str] = Counter()
    for pattern in (SPEAKER_RE, NAME_MARKER_RE, CHARACTER_CONTEXT_RE):
        for match in pattern.finditer(text):
            name = match.group(1)
            if valid_name(name):
                names[name] += 1
    return names


def extract_locations(sentences: Iterable[str]) -> Counter[str]:
    locations: Counter[str] = Counter()
    for sentence in sentences:
        for match in LOCATION_RE.finditer(sentence):
            value = match.group(1)
            if len(value) <= 12:
                locations[value] += 1
    return locations


def top_keyword_records(novel_id: str, kind: str, counter: Counter[str], chapters_by_key: dict[str, list[dict[str, object]]], limit: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for key, count in counter.most_common(limit):
        evidence = chapters_by_key.get(key, [])[:5]
        records.append(
            {
                "id": f"{novel_id}/{kind}-{key}",
                "novel_id": novel_id,
                "title": key,
                "summary": f"自动提取到 {count} 次相关出现，需在精读阶段校验。",
                "evidence": evidence,
                "confidence": min(0.95, 0.35 + count / 20),
                "tags": ["auto", kind],
                "created_at": now_iso(),
            }
        )
    return records


def write_semantic_outputs(book_dir: Path, bundle: dict[str, object]) -> None:
    dump_json(book_dir / "knowledge_bundle.json", bundle)
    dump_jsonl(book_dir / "semantic_chapters.jsonl", bundle.get("chapters", []))
    dump_jsonl(book_dir / "characters.jsonl", bundle.get("characters", []))
    dump_jsonl(book_dir / "arcs.jsonl", bundle.get("arcs", []))
    dump_jsonl(book_dir / "scenes.jsonl", bundle.get("scenes", []))
    dump_jsonl(book_dir / "systems.jsonl", bundle.get("systems", []))
    dump_jsonl(book_dir / "style_notes.jsonl", bundle.get("style_notes", []))
