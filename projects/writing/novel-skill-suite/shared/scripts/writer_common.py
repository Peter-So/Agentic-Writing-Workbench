from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from common import dump_json, dump_jsonl, normalize_whitespace, now_iso

CHINESE_NAME_RE = re.compile(r"^[\u4e00-\u9fff]{2,4}$")
REPEATED_ADVERB_RE = re.compile(r"^([\u4e00-\u9fff])\1(地)?$")
BAD_NAME_SUBSTRINGS = (
    "低声", "微笑", "老者", "几位", "两位", "二位", "当即", "自然知", "笑的回", "动声色", "无表情",
    "不管怎么", "让韩立", "冲韩立", "的吩咐", "二话不", "了起", "了出", "了过", "韩立点", "韩立微微",
    "韩立一", "不动声色", "几位道", "两位道", "道友", "师弟", "师兄", "师叔", "师父", "前辈",
    "真人", "掌门", "大师", "老僧", "大夫", "公子", "夫人", "姑娘", "少爷", "小姐",
    "小师弟", "小师妹", "含笑", "淡淡", "冷冷", "大声", "低低", "突然", "忽然", "因为", "所以",
    "只是", "然而", "但是", "不过", "于是", "已经", "正在", "还是", "没有", "知道",
    "轻轻", "缓缓", "慢慢", "静静", "呐呐", "幽幽", "低低", "连忙", "猛然", "愕然", "朗声",
    "皱眉", "冷笑", "正色", "只得", "然后", "一时", "个不停", "了出来", "话未", "言自语",
    "在一旁", "对小环", "与苍松", "是苍松", "有人知", "怎么知", "才缓缓", "管怎么",
    "青云门", "门派", "宗门", "山门", "谷口", "大殿", "庙宇", "村子", "城中", "军中", "朝中",
)
BAD_NAME_SUFFIXES = (
    "点", "回", "起", "出", "过", "知", "一", "声", "道", "说", "问", "看", "听", "笑", "微", "低", "高",
    "地", "着", "了", "未", "不停", "出来", "起来", "过去", "几声", "一旁", "自语", "缓缓", "呐呐",
)
BAD_NAME_PREFIXES = (
    "我", "你", "他", "她", "它", "这", "那", "其", "某", "谁",
    "和", "的", "是", "人", "向", "冲", "跟", "被", "与", "从", "到", "在", "对",
    "让", "给", "把", "来", "往", "回", "再", "还", "又", "正", "忽", "突", "立",
    "伸", "拉", "看", "听", "说", "问", "答", "笑", "点", "带",
    "就", "却", "才", "并", "将", "因", "为", "像", "仍", "都", "便",
    "了", "而", "便", "若", "可", "且", "则", "既", "已", "唯",
)
STOP_NAME_SET = {
    "低声", "微笑", "老者", "几位", "两位", "二位", "当即", "自然知", "笑的回", "动声色的", "无表情的",
    "不管怎么", "让韩立", "冲韩立", "的吩咐", "二话不", "了起", "了出", "了过", "韩立点", "韩立微微",
    "韩立一", "不动声色", "几位道友", "两位道友", "道友", "师弟", "师兄", "师叔", "师父", "前辈",
    "真人", "掌门", "大师", "老僧", "大夫", "公子", "夫人", "姑娘", "少爷", "小姐",
    "小师弟", "小师妹", "含笑", "淡淡", "冷冷", "大声", "低低", "突然", "忽然", "因为", "所以",
    "只是", "然而", "但是", "不过", "于是", "已经", "正在", "还是", "没有", "知道", "不要", "一样",
    "的话", "吩咐", "回道", "说道", "问道", "答道", "喝道", "笑道", "说道", "低声", "微微", "面无表情",
    "青云门", "门派", "宗门", "山门", "谷口", "大殿", "庙宇", "村子", "城中", "军中", "朝中",
    "当下", "只得", "一时", "后来", "然后", "于是", "随即", "片刻", "半晌", "立时", "马上", "连忙",
    "赶紧", "急忙", "慢慢", "缓缓", "轻轻", "忽然", "突然", "不久", "如今", "今日", "昨日", "明日",
    "此时", "眼前", "心中", "身前", "面前", "不料", "看来", "果然", "原来", "其实",
    "静静地", "幽幽地", "低低地", "缓缓地", "呐呐", "轻轻", "个不停", "话未", "厉缓缓", "皱眉",
    "冷笑", "朗声", "连忙", "猛然", "愕然", "正色", "然后", "有人知", "怎么知", "才缓缓",
    "可以", "却是", "无数", "在一旁", "对小环", "与苍松", "是苍松", "了出来", "了几声",
    "言自语", "管怎么", "松道人",
}
ROLE_MARKERS = {
    "mentor": ("师父", "师叔", "掌门", "真人", "长老", "前辈", "大师", "老僧", "大夫", "道长"),
    "ally": ("师兄", "师弟", "同门", "朋友", "好友", "相助", "同行", "伙伴", "帮忙", "照应", "护着"),
    "rival": ("对手", "敌", "斗", "交手", "比试", "追杀", "围杀", "仇", "邪", "魔", "争夺", "冲突", "妖女", "魔教", "凶"),
    "guide": ("引路", "指点", "提醒", "教他", "传授", "示意", "嘱咐", "告诫"),
    "romance": ("姑娘", "少女", "女子", "师妹", "夫人", "小姐", "柔声", "目光", "心动", "脸红", "美丽", "担忧", "红衣"),
}
GENRE_KEYWORDS = {
    "修仙/玄幻": ("修炼", "境界", "灵气", "灵石", "法宝", "丹药", "门派", "宗门", "仙", "魔", "妖", "突破", "炼气", "筑基"),
    "江湖/武侠": ("江湖", "武功", "刀", "剑", "比武", "门派", "帮派", "仇杀", "掌门", "师门"),
    "探墓/冒险": ("墓", "机关", "风水", "盗墓", "鬼", "尸", "古墓", "探险", "藏宝", "遗迹"),
    "权谋/历史": ("朝廷", "军", "将军", "王", "帝", "谋", "政", "天下", "城", "国", "战"),
    "悬疑/刑侦": ("案件", "线索", "真相", "侦查", "警察", "凶手", "调查", "嫌疑", "秘密", "证据"),
}


@dataclass
class Card:
    name: str
    role_guess: str
    function: str
    drive: str
    conflict_focus: str
    growth_axis: str
    evidence: list[dict[str, object]]


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def save_md(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def is_clean_name(name: str) -> bool:
    if not CHINESE_NAME_RE.match(name):
        return False
    if "的" in name:
        return False
    if REPEATED_ADVERB_RE.match(name):
        return False
    if len(name) == 3 and name.endswith("道人"):
        return False
    if name in STOP_NAME_SET:
        return False
    if any(name.startswith(prefix) for prefix in BAD_NAME_PREFIXES):
        return False
    if any(name.endswith(suffix) for suffix in BAD_NAME_SUFFIXES):
        return False
    if any(fragment in name for fragment in BAD_NAME_SUBSTRINGS):
        return False
    return True


def evidence_text(evidence: dict[str, object]) -> str:
    return str(evidence.get("excerpt", ""))


def score_character(candidate: dict[str, object]) -> tuple[int, int, int]:
    evidence = candidate.get("evidence", [])
    evidence_count = len(evidence) if isinstance(evidence, list) else 0
    chapter_ids = {
        item.get("chapter_id")
        for item in evidence
        if isinstance(item, dict)
    }
    chapter_diversity = len(chapter_ids)
    role_hits = 0
    for item in evidence:
        text = evidence_text(item)
        for markers in ROLE_MARKERS.values():
            if any(marker in text for marker in markers):
                role_hits += 1
                break
    return (evidence_count, chapter_diversity, role_hits)


def chapter_sample(chapters: list[dict[str, object]], index: int) -> dict[str, object] | None:
    if not chapters:
        return None
    idx = max(0, min(len(chapters) - 1, index))
    return chapters[idx]


def chapter_by_quantile(chapters: list[dict[str, object]], ratio: float) -> dict[str, object] | None:
    if not chapters:
        return None
    idx = int(round((len(chapters) - 1) * ratio))
    return chapter_sample(chapters, idx)


def top_style_markers(style_notes: list[dict[str, object]], limit: int = 5) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for note in style_notes:
        title = str(note.get("title", ""))
        marker = title.split("式转折/提示", 1)[0]
        if marker:
            counter[marker] += 1
    return counter.most_common(limit)


def infer_genre(bundle: dict[str, object]) -> tuple[str, str]:
    systems = bundle.get("systems", [])
    texts = " ".join(str(item.get("title", "")) + " " + str(item.get("summary", "")) for item in systems if isinstance(item, dict))
    best = ("通用冒险", 0, "系统关键词不足，采用通用模板。")
    for genre, words in GENRE_KEYWORDS.items():
        score = sum(texts.count(word) for word in words)
        if score > best[1]:
            best = (genre, score, f"系统词中命中 {genre} 关键词。")
    if best[1] == 0:
        return ("通用冒险", "系统关键词不足，采用通用模板。")
    return best[0], best[2]


def role_guess_from_evidence(name: str, evidence: list[dict[str, object]], rank: int, genre: str) -> str:
    texts = " ".join(evidence_text(item) for item in evidence)
    if rank == 1 and len(evidence) >= 500:
        if genre in ("修仙/玄幻", "江湖/武侠", "探墓/冒险", "权谋/历史", "悬疑/刑侦"):
            return "主角/叙事承载者"
    role_scores: Counter[str] = Counter()
    for item in evidence:
        text = evidence_text(item)
        for role, markers in ROLE_MARKERS.items():
            role_scores[role] += sum(text.count(marker) for marker in markers)
    if role_scores:
        priority = {"rival": 5, "romance": 4, "ally": 3, "guide": 2, "mentor": 1}
        role = max(role_scores, key=lambda key: (role_scores[key], priority[key]))
        if role_scores[role] > 0:
            return {
                "mentor": "导师/权威压强位",
                "ally": "伙伴/同盟位",
                "rival": "对手/压力位",
                "guide": "引路/提示位",
                "romance": "情感/关系位",
            }[role]
    if any(term in name for term in ("师", "掌", "道", "僧", "老", "前")):
        return "权威/师门位"
    if "鬼" in name or "魔" in name or "邪" in name:
        return "对抗/异质位"
    return "功能角色位"


def drive_for_genre(genre: str, role_guess: str) -> str:
    if genre == "修仙/玄幻":
        return "求生、求强、求突破"
    if genre == "江湖/武侠":
        return "求名、求义、求活路"
    if genre == "探墓/冒险":
        return "求生、求财、求真相"
    if genre == "权谋/历史":
        return "求局、求势、求存活"
    if genre == "悬疑/刑侦":
        return "求真、求证据、求破局"
    if "主角" in role_guess:
        return "推进目标、承受代价、完成阶段成长"
    return "维持张力、推动冲突、补充关系"


def conflict_focus_for_genre(genre: str) -> str:
    return {
        "修仙/玄幻": "资源、境界、门派秩序、隐藏代价",
        "江湖/武侠": "恩怨、立场、名声、刀剑压迫",
        "探墓/冒险": "机关、未知、时间压力、求生代价",
        "权谋/历史": "势力、阵营、情报、身份风险",
        "悬疑/刑侦": "线索、误导、真相、遮蔽层",
    }.get(genre, "目标、规则、代价、信息差")


def growth_axis_for_genre(genre: str, role_guess: str) -> str:
    if genre == "修仙/玄幻":
        return "从被动应对到主动布势，从低阶视野走向规则掌控"
    if genre == "江湖/武侠":
        return "从求活到求名，从个人恩怨走向更大的立场"
    if genre == "探墓/冒险":
        return "从冒险试探到稳住底牌，从胆量取胜到经验取胜"
    if genre == "权谋/历史":
        return "从被局裹挟到会借势设局，从单点反应到全局判断"
    if genre == "悬疑/刑侦":
        return "从收集证据到识别真相结构，从追线索到抓逻辑"
    if "主角" in role_guess:
        return "从单线推进到多线统筹，从情绪驱动到策略驱动"
    return "从单一功能到成为局中关键节点"


def select_character_cards(bundle: dict[str, object], genre: str, limit: int = 5) -> list[dict[str, object]]:
    candidates = [item for item in bundle.get("characters", []) if isinstance(item, dict) and is_clean_name(str(item.get("title", "")))]
    candidates = sorted(candidates, key=lambda item: score_character(item), reverse=True)
    cards: list[dict[str, object]] = []
    for rank, candidate in enumerate(candidates[:limit], start=1):
        name = str(candidate.get("title", ""))
        evidence = candidate.get("evidence", [])
        if not isinstance(evidence, list):
            evidence = []
        role_guess = role_guess_from_evidence(name, evidence, rank, genre)
        card = Card(
            name=name,
            role_guess=role_guess,
            function={
                "主角/叙事承载者": "承担主线推进与阶段变化，作为读者进入故事的第一视角。",
                "导师/权威压强位": "提供规则、门槛、限制或关键指引，让主角进入更深层局面。",
                "伙伴/同盟位": "提供陪伴、照应、信息交换或关系镜像，放大主角处境。",
                "对手/压力位": "制造对抗、压迫、试探或清算，推动局势升级。",
                "引路/提示位": "把关键线索、规则或路径递给主角，推动转场。",
                "情感/关系位": "承担人物关系变化和情绪牵引。",
                "权威/师门位": "代表秩序、门槛与规训，是人物成长的边界。",
                "对抗/异质位": "代表不稳定风险、异质力量或反向目标。",
            }.get(role_guess, "承担功能性推进，推动章节局势变化。"),
            drive=drive_for_genre(genre, role_guess),
            conflict_focus=conflict_focus_for_genre(genre),
            growth_axis=growth_axis_for_genre(genre, role_guess),
            evidence=evidence[:5],
        )
        cards.append({
            "name": card.name,
            "role_guess": card.role_guess,
            "function": card.function,
            "drive": card.drive,
            "conflict_focus": card.conflict_focus,
            "growth_axis": card.growth_axis,
            "evidence": card.evidence,
        })
    return cards


def infer_role_cards(bundle: dict[str, object], genre: str) -> list[dict[str, object]]:
    systems = bundle.get("systems", [])
    scenes = bundle.get("scenes", [])
    style_notes = bundle.get("style_notes", [])
    top_markers = [m for m, _ in top_style_markers(style_notes, 4)]
    top_settings = Counter(str(item.get("setting", "")) for item in scenes if isinstance(item, dict) and str(item.get("setting", "")))
    setting_text = "、".join([s for s, _ in top_settings.most_common(4)])
    genre_role_map = {
        "修仙/玄幻": [
            ("主角轴", "从入门、受压、试探到反制，负责承接升级线。", "求生、求强、求突破", "境界、门派、资源、代价", "从被动应对到主动布势"),
            ("师门/导师轴", "负责设门槛、传规则、给任务、立边界。", "维持秩序、传授方法、控制节奏", "规训、门槛、身份", "从外部规则到被纳入规则"),
            ("对抗/敌手轴", "负责制造压迫、冲突、追击与反击窗口。", "压制、试探、夺取主动权", "资源、地位、秘密", "从局部压迫到全面对抗"),
            ("同盟/伙伴轴", "负责信息交换、照应、镜像和节奏缓冲。", "互补、并肩、共享风险", "信任、误解、立场", "从陪衬到独立承担局势"),
        ],
        "探墓/冒险": [
            ("主角轴", "从好奇、下场、失措到摸清规则与活下来。", "求生、求财、求真相", "机关、未知、时间压力", "从试探到掌控现场"),
            ("向导/线索轴", "负责把入口、传闻或古旧规则递给主角。", "提示路径、规避风险", "隐秘、误导、代价", "从旁观者到关键触发器"),
            ("威胁/陷阱轴", "负责制造场景压迫和死亡风险。", "卡住推进、逼迫选择", "机关、幽暗、禁忌", "从环境压力到行动压力"),
            ("同伴/补位轴", "负责协助、抬人、补缺、接话。", "分担风险、托底", "分歧、恐惧、利益", "从陪跑到关键节点"),
        ],
        "江湖/武侠": [
            ("主角轴", "负责把恩怨、立场和名声压成一个人承担的选择。", "求名、求义、求活路", "门派、仇怨、声望", "从个人气力到立场判断"),
            ("师门/秩序轴", "负责门规、等级、名分、传承。", "守住秩序、维护名分", "规矩、传承、偏见", "从约束力量到被角色重新定义"),
            ("对手/宿敌轴", "负责把江湖矛盾具象化。", "夺势、压制、清算", "旧怨、新仇、争名", "从小冲突升级到生死局"),
            ("旁观/调停轴", "负责制造观感、传话、风向和节奏变化。", "解释、调停、制造误会", "舆论、面子、人情", "从旁观到介入局面"),
        ],
    }
    roles = genre_role_map.get(genre, [
        ("主角轴", "承担主线推进与成长变化。", "推进目标、承受代价", "目标、规则、代价", "从反应到主动"),
        ("规则/权威轴", "负责设规矩、设障碍、发任务。", "维持秩序、限定边界", "身份、门槛、约束", "从限制到被破解"),
        ("对抗轴", "负责制造压力与升级。", "推进冲突、逼出选择", "冲突、风险、误判", "从单点冲突到连锁冲突"),
        ("缓冲轴", "负责托底、照应、补信息。", "缓和张力、提供对照", "关系、误解、协作", "从功能位到情感位"),
    ])
    cards: list[dict[str, object]] = []
    for role_name, function, drive, conflict_focus, growth_axis in roles[:4]:
        cards.append({
            "role_name": role_name,
            "function": function,
            "drive": drive,
            "conflict_focus": conflict_focus,
            "growth_axis": growth_axis,
            "evidence": [],
            "anchors": {
                "settings": setting_text,
                "style_markers": top_markers,
            },
        })
    return cards


def chapter_beats(chapters: list[dict[str, object]]) -> list[dict[str, object]]:
    if not chapters:
        return []
    picks = [0, int((len(chapters) - 1) * 0.1), int((len(chapters) - 1) * 0.25), int((len(chapters) - 1) * 0.5), int((len(chapters) - 1) * 0.75), len(chapters) - 1]
    beats = []
    names = ["开局钩子", "入局", "加压", "转折", "失衡", "收束"]
    functions = [
        "把世界规则、核心矛盾和起始处境放在读者面前。",
        "让主角真正进入故事机器，接触第一个不可逆约束。",
        "把局势从可控推向不可控，逼出第一次代价。",
        "让中段矛盾翻面，关系、目标或规则发生变化。",
        "把风险、损失、秘密或对抗推到高点。",
        "留下新阶段入口，形成可继续扩展的尾钩。",
    ]
    seen = set()
    for beat_name, idx, function in zip(names, picks, functions):
        if idx in seen:
            continue
        seen.add(idx)
        chapter = chapters[idx]
        text = normalize_whitespace(str(chapter.get("summary", "")) or " ".join(chapter.get("events", [])[:2]))
        beats.append({
            "beat": beat_name,
            "chapter_id": chapter.get("chapter_id", ""),
            "chapter_title": chapter.get("title", ""),
            "summary": text[:240],
            "function": function,
        })
    return beats


def plot_template(bundle: dict[str, object], genre: str) -> dict[str, object]:
    chapters = list(bundle.get("chapters", []))
    beats = chapter_beats(chapters)
    genre_formula = {
        "修仙/玄幻": "开局建立规则与门槛，中段靠资源/境界/门派压强升级，结尾用更大层级的规则打开下一阶段。",
        "江湖/武侠": "开局先立人物处境和门派关系，中段靠比试/追杀/误会推进，结尾以立场变化或新仇旧怨收束。",
        "探墓/冒险": "开局用传闻或入口诱发下场，中段靠机关、陷阱、同伴分歧升级，结尾留下一层更深秘密。",
        "权谋/历史": "开局用阵营和局势定框，中段以试探、借势、反咬升级，结尾把关系重组为新局面。",
        "悬疑/刑侦": "开局抛出谜面，中段以线索冲突和误导推进，结尾用真相的局部揭示带出下一层问题。",
    }.get(genre, "开局先立规则，中段用冲突和代价推进，结尾留新谜面或新目标。")
    examples = [f"{beat['beat']}：{beat['summary']}" for beat in beats]
    pattern = " -> ".join([beat["beat"] for beat in beats]) if beats else "开局 -> 入局 -> 加压 -> 转折 -> 失衡 -> 收束"
    return {
        "title": "剧情模板",
        "summary": genre_formula,
        "pattern": pattern,
        "examples": examples,
        "notes": [
            "把阶段性解决写成新麻烦，而不是彻底结束。",
            "每一段都尽量有一个可说清的变化：身份、位置、信息、代价或关系。",
        ],
    }


def conflict_template(bundle: dict[str, object], genre: str) -> dict[str, object]:
    top_systems = Counter(str(item.get("title", "")) for item in bundle.get("systems", []) if isinstance(item, dict) and str(item.get("title", "")))
    systems_text = "、".join([s for s, _ in top_systems.most_common(6)])
    genre_summary = {
        "修仙/玄幻": "冲突靠资源、境界和门派规则压出来，人物每前进一步都要付出代价。",
        "江湖/武侠": "冲突靠恩怨、声望和立场推进，局势往往不是打赢就算完。",
        "探墓/冒险": "冲突靠未知、机关和时间压力推进，活下来本身就是胜利。",
        "权谋/历史": "冲突靠阵营、情报和身份风险推进，输赢常常藏在前一层布局里。",
        "悬疑/刑侦": "冲突靠线索、误导和真相遮蔽推进，越接近答案越危险。",
    }.get(genre, "冲突靠目标、规则和代价压出来。")
    return {
        "title": "冲突模板",
        "summary": genre_summary,
        "pattern": "目标受阻 -> 规则施压 -> 试探反应 -> 代价出现 -> 局势升级 -> 阶段清算 -> 新压力进入",
        "examples": [
            f"高频系统词：{systems_text or '无明显系统词'}",
            "常见写法是先把人物逼进一个有限场域，再用规则和代价迫使其做选择。",
        ],
        "notes": [
            "把冲突写成资源、规则、身份与关系的联动，不只写动作。",
            "每次升级最好让角色失去一点什么：时间、信任、位置、秘密或底牌。",
        ],
    }


def scene_template(bundle: dict[str, object], genre: str) -> dict[str, object]:
    scenes = [item for item in bundle.get("scenes", []) if isinstance(item, dict)]
    top_settings = Counter(str(item.get("setting", "")) for item in scenes if str(item.get("setting", "")))
    settings = [setting for setting, _ in top_settings.most_common(6) if setting]
    sensory = Counter()
    for item in scenes:
        for note in item.get("sensory_notes", []) or []:
            sensory[str(note)] += 1
    sensory_palette = [note for note, _ in sensory.most_common(5)]
    genre_summary = {
        "修仙/玄幻": "场景通常先交代空间层级与门槛，再让人物在规则中行动。",
        "江湖/武侠": "场景通常围绕门派、院落、山路、酒馆、比试台等压出人际关系。",
        "探墓/冒险": "场景通常围绕洞穴、墓室、荒野、机关和夜色营造压迫感。",
        "权谋/历史": "场景通常围绕厅堂、军营、城池、朝堂和书信信息差组织。",
        "悬疑/刑侦": "场景通常围绕现场、线索点、临场问答和时间限制推进。",
    }.get(genre, "场景通常先立空间，再立动作，再立风险。")
    examples = settings[:4] if settings else ["先给空间，再给人物，再给行动，再留一个未完成的出口。"]
    return {
        "title": "场景模板",
        "summary": genre_summary,
        "pattern": "空间入口 -> 站位/陈设 -> 动作推进 -> 阻碍出现 -> 信息露出 -> 留尾钩",
        "examples": examples,
        "notes": [
            f"高频感官词：{', '.join(sensory_palette) if sensory_palette else '无明显感官词'}",
            "尽量让场景承担功能，不只是装饰。",
        ],
    }


def rhythm_template(bundle: dict[str, object], genre: str) -> dict[str, object]:
    chapters = list(bundle.get("chapters", []))
    avg_len = 0
    avg_events = 0
    if chapters:
        avg_len = int(sum(int(ch.get("chapter_length", 0)) for ch in chapters) / len(chapters))
        avg_events = int(sum(len(ch.get("events", [])) if isinstance(ch.get("events", []), list) else 0 for ch in chapters) / len(chapters))
    markers = [marker for marker, _ in top_style_markers(bundle.get("style_notes", []), 6)]
    density = "紧凑" if avg_len < 3500 else "中等" if avg_len < 7000 else "密实"
    genre_summary = {
        "修仙/玄幻": "节奏常见做法是：先铺规则，再给压力，再给一次突破或反噬。",
        "江湖/武侠": "节奏常见做法是：先稳住人物关系，再让事件把关系拧紧，最后留下一层后患。",
        "探墓/冒险": "节奏常见做法是：先给入口，再给危险，再给短暂收益，最后回到更深的风险。",
        "权谋/历史": "节奏常见做法是：先让局面看起来可控，再把信息差一层层揭开。",
        "悬疑/刑侦": "节奏常见做法是：每段都给一个新线索，但同时制造一个新误导。",
    }.get(genre, "节奏常见做法是：先铺垫、再加压、再反转、再留尾钩。")
    pattern = f"平均章节长度约 {avg_len} 字，单章事件密度约 {avg_events} 条，整体节奏偏 {density}。"
    return {
        "title": "节奏模板",
        "summary": genre_summary,
        "pattern": pattern,
        "examples": [
            f"常用转折标记：{', '.join(markers) if markers else '无明显标记'}",
            "章尾最好留一个未回答的问题、未完成的动作或未揭开的信息。",
        ],
        "notes": [
            "把信息投放拆开：先让读者知道一半，再用下一段补足另一半。",
            "不要把所有解释一次性放完，保留可追的空缺。",
        ],
    }


def prompt_seed(book_title: str, genre: str, character_cards: list[dict[str, object]], plot: dict[str, object], conflict: dict[str, object], scene: dict[str, object], rhythm: dict[str, object]) -> str:
    return (
        f"请基于《{book_title}》提炼出的结构资产，创作一部原创{genre}小说。"
        "主角采用“主角轴”的功能设计推进，但情节、人物、设定与场景必须全新原创。"
        f"遵循剧情模板：{plot['summary']}；"
        f"核心冲突：{conflict['summary']}；"
        f"场景写法：{scene['summary']}；"
        f"节奏要求：{rhythm['summary']}。"
        "输出时要有明确开局钩子、阶段升级、代价和尾钩。"
    )


def book_markdown(asset: dict[str, object]) -> str:
    lines = []
    lines.append(f"# {asset['book_title']} 作家卡")
    lines.append("")
    lines.append(f"- 题材判断：{asset['genre_guess']}")
    lines.append(f"- 判断理由：{asset['genre_reason']}")
    lines.append("")
    lines.append("## 人物卡")
    for card in asset["character_cards"]:
        lines.append(f"### {card['name']}")
        lines.append(f"- 角色判断：{card['role_guess']}")
        lines.append(f"- 功能：{card['function']}")
        lines.append(f"- 驱动力：{card['drive']}")
        lines.append(f"- 冲突焦点：{card['conflict_focus']}")
        lines.append(f"- 成长轴：{card['growth_axis']}")
        if card.get("evidence"):
            lines.append(f"- 证据：{card['evidence'][0].get('excerpt', '')[:160]}")
        lines.append("")
    lines.append("## 功能位人物卡")
    for card in asset["role_cards"]:
        lines.append(f"### {card['role_name']}")
        lines.append(f"- 功能：{card['function']}")
        lines.append(f"- 驱动力：{card['drive']}")
        lines.append(f"- 冲突焦点：{card['conflict_focus']}")
        lines.append(f"- 成长轴：{card['growth_axis']}")
        anchors = card.get("anchors", {})
        if isinstance(anchors, dict):
            lines.append(f"- 空间锚点：{anchors.get('settings', '')}")
            markers = anchors.get('style_markers', [])
            if isinstance(markers, list):
                marker_text = ", ".join(f"{m[0]}({m[1]})" if isinstance(m, tuple) and len(m) == 2 else str(m) for m in markers)
            else:
                marker_text = str(markers)
            lines.append(f"- 节奏锚点：{marker_text}")
        lines.append("")
    for key in ["plot_template", "conflict_template", "scene_template", "rhythm_template"]:
        block = asset[key]
        lines.append(f"## {block['title']}")
        lines.append(f"- 结论：{block['summary']}")
        lines.append(f"- 模式：{block['pattern']}")
        lines.append("- 例子：")
        for example in block.get("examples", [])[:4]:
            lines.append(f"  - {example}")
        if block.get("notes"):
            lines.append("- 备注：")
            for note in block["notes"]:
                lines.append(f"  - {note}")
        lines.append("")
    lines.append("## 直接开写提示")
    lines.append(asset["prompt_seed"])
    lines.append("")
    return "\n".join(lines)


def corpus_md(playbook: dict[str, object]) -> str:
    lines = []
    lines.append("# 语料作家总手册")
    lines.append("")
    lines.append(f"- 语料数量：{playbook['book_count']}")
    lines.append(f"- 题材分布：{', '.join(f'{k}({v})' for k, v in playbook['genre_counts'].items())}")
    lines.append("")
    lines.append("## 共同人物位")
    for item in playbook["common_roles"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 共同剧情模板")
    for item in playbook["common_plots"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 共同冲突引擎")
    for item in playbook["common_conflicts"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 共同场景骨架")
    for item in playbook["common_scenes"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 共同节奏动作")
    for item in playbook["common_rhythms"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def build_book_asset(book_dir: Path, output_root: Path) -> dict[str, object]:
    bundle = load_json(book_dir / "knowledge_bundle.json")
    manifest = bundle.get("manifest", {})
    chapters = list(bundle.get("chapters", []))
    book_title = book_dir.name
    if chapters and isinstance(chapters[0], dict):
        source_file = str(chapters[0].get("file_path", ""))
    else:
        source_file = ""
    genre, genre_reason = infer_genre(bundle)
    character_cards = select_character_cards(bundle, genre, limit=5)
    role_cards = infer_role_cards(bundle, genre)
    plot = plot_template(bundle, genre)
    conflict = conflict_template(bundle, genre)
    scene = scene_template(bundle, genre)
    rhythm = rhythm_template(bundle, genre)
    prompt = prompt_seed(book_title, genre, character_cards, plot, conflict, scene, rhythm)
    asset = {
        "novel_id": book_dir.name,
        "book_title": book_title,
        "source_book_dir": str(book_dir.resolve()),
        "source_file": source_file,
        "genre_guess": genre,
        "genre_reason": genre_reason,
        "character_cards": character_cards,
        "role_cards": role_cards,
        "plot_template": plot,
        "conflict_template": conflict,
        "scene_template": scene,
        "rhythm_template": rhythm,
        "prompt_seed": prompt,
        "source_manifest": manifest,
        "generated_at": now_iso(),
    }
    out_dir = output_root / book_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    dump_json(out_dir / "book_asset.json", asset)
    save_md(out_dir / "book_asset.md", book_markdown(asset))
    dump_jsonl(out_dir / "character_cards.jsonl", character_cards)
    dump_jsonl(out_dir / "role_cards.jsonl", role_cards)
    return asset


def build_corpus_playbook(book_assets: list[dict[str, object]], output_root: Path) -> dict[str, object]:
    genre_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    plot_counts: Counter[str] = Counter()
    conflict_counts: Counter[str] = Counter()
    scene_counts: Counter[str] = Counter()
    rhythm_counts: Counter[str] = Counter()
    for asset in book_assets:
        genre_counts[str(asset["genre_guess"])] += 1
        for card in asset["role_cards"]:
            role_counts[str(card["role_name"])] += 1
        plot_counts[str(asset["plot_template"]["summary"])] += 1
        conflict_counts[str(asset["conflict_template"]["summary"])] += 1
        scene_counts[str(asset["scene_template"]["summary"])] += 1
        for note in asset["rhythm_template"].get("examples", []):
            rhythm_counts[str(note)] += 1
    playbook = {
        "generated_at": now_iso(),
        "book_count": len(book_assets),
        "genre_counts": dict(genre_counts),
        "common_roles": [f"{k} ({v})" for k, v in role_counts.most_common(8)],
        "common_plots": [f"{k} ({v})" for k, v in plot_counts.most_common(8)],
        "common_conflicts": [f"{k} ({v})" for k, v in conflict_counts.most_common(8)],
        "common_scenes": [f"{k} ({v})" for k, v in scene_counts.most_common(8)],
        "common_rhythms": [f"{k} ({v})" for k, v in rhythm_counts.most_common(8)],
        "books": [
            {
                "novel_id": asset["novel_id"],
                "book_title": asset["book_title"],
                "genre_guess": asset["genre_guess"],
                "book_asset_path": str((output_root / asset["novel_id"] / "book_asset.json").resolve()),
            }
            for asset in book_assets
        ],
    }
    corpus_dir = output_root / "_corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    dump_json(corpus_dir / "writer_playbook.json", playbook)
    save_md(corpus_dir / "writer_playbook.md", corpus_md(playbook))
    return playbook
