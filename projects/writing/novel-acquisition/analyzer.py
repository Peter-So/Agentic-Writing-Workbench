#!/usr/bin/env python3
"""
网文精华分析器 v2 — 章节感知 + 跨章伏笔配对 + 比例采样

四大维度：
1. 场景描写 — 三区比例采样 (前/中/后)
2. 内心活动 — 三区比例采样
3. 人物描写 — 三区比例采样
4. 伏笔反转 — 章节索引 → 前30%捕获「未解之谜」→ 后70%检测「回收信号」→ 配对打分

输出结构化 JSON，供作家技能库学习。
"""

import json
import os
import re
import sys
from pathlib import Path
from collections import defaultdict, Counter
from itertools import islice

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import NOVEL_ACQUISITION_DIR

# ── Config ──────────────────────────────────────────────
NOVEL_DIR = NOVEL_ACQUISITION_DIR / "novels"
OUTPUT_DIR = NOVEL_ACQUISITION_DIR / "extracted"
MIN_PASSAGE_LEN = 30
MAX_PASSAGE_LEN = 3000
SAMPLES_PER_DIM = 50       # 每维度总配额
SAMPLES_PER_ZONE = 17      # 每区配额 (3区 ≈ 51, 取50)
TWIST_PAIRS_MAX = 30       # 伏笔配对最大输出数

# ── 章节检测 ────────────────────────────────────────────

CHAPTER_PATTERNS = [
    # 1、第一节：标题 / 23、第二十三节：标题 (网文常见)
    re.compile(r'^[ \t]*\d+[、，,.]?\s*第[零一二三四五六七八九十百千万\d]+[章回节卷集部篇]'),
    # 第四章 标题 / 第4章 标题 / 第四章：标题
    re.compile(r'^[ \t]*第[零一二三四五六七八九十百千万\d]+[章回节卷集部篇]'),
    # 数字.章标题 如 "001 章名"  "001.章名"
    re.compile(r'^[ \t]*\d{2,4}[\.\s、]'),
    # Chapter 4 / Ch.4
    re.compile(r'^[ \t]*[Cc]hapter\s*\d+'),
    # 卷X 第一章
    re.compile(r'^[ \t]*[卷部篇辑][\s]*[零一二三四五六七八九十百千万\d]+.*第[零一二三四五六七八九十百千万\d]+[章回节]'),
    # 序章/楔子/尾声/番外
    re.compile(r'^[ \t]*(?:序章|楔子|前言|尾声|后记|番外|终章|大结局|结局)'),
    # 分隔线形式的章节标记
    re.compile(r'^[ \t]*[=\-—＊\*]{3,}.*第[零一二三四五六七八九十百千万\d]+[章回节卷]'),
]

def detect_chapters(text):
    """检测章节边界，返回 [(chapter_index, start_pos, heading), ...]"""
    lines = text.split('\n')
    chapters = []
    line_positions = [0]
    pos = 0
    for line in lines:
        line_positions.append(pos)
        pos += len(line) + 1  # +1 for \n

    for i, line in enumerate(lines):
        line = line.strip()
        if not line or len(line) > 100:
            continue
        for pat in CHAPTER_PATTERNS:
            if pat.match(line):
                chapters.append({
                    "idx": len(chapters),
                    "heading": line[:60],
                    "line": i,
                    "pos": line_positions[i],
                })
                break

    return chapters


def get_chapter_text(text, chapters, ch_idx):
    """获取指定章节的完整文本"""
    if ch_idx >= len(chapters):
        return ""
    start = chapters[ch_idx]["pos"]
    if ch_idx + 1 < len(chapters):
        end = chapters[ch_idx + 1]["pos"]
    else:
        end = len(text)
    return text[start:end]


# ── 辅助：分区 ───────────────────────────────────────────

def partition_paragraphs(text, chapters):
    """将全文按章拆成段落，返回三大区 (early, mid, late) 的段落列表"""
    if not chapters:
        # 无章节检测，按字符数硬分三区
        all_paras = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
        n = len(all_paras)
        return (
            all_paras[:n//3],
            all_paras[n//3:2*n//3],
            all_paras[2*n//3:],
        )

    n_ch = len(chapters)
    early_cut = max(1, n_ch // 3)
    late_cut = max(early_cut + 1, 2 * n_ch // 3)

    def ch_range(start, end):
        paras = []
        for ci in range(start, min(end, n_ch)):
            ch_text = get_chapter_text(text, chapters, ci)
            paras.extend(p.strip() for p in re.split(r'\n+', ch_text) if p.strip())
        return paras

    return (
        ch_range(0, early_cut),
        ch_range(early_cut, late_cut),
        ch_range(late_cut, n_ch),
    )


def proportional_sample(matches, target):
    """从匹配列表中均匀采 target 个（不取前N）"""
    if len(matches) <= target:
        return matches
    step = len(matches) / target
    return [matches[int(i * step)] for i in range(target)]


# ── 维度1：场景描写 ─────────────────────────────────────

SCENE_KEYWORDS = [
    "远处", "近处", "四周", "前方", "身后", "头顶", "脚下", "天空",
    "大地", "山脉", "河流", "森林", "沙漠", "海洋", "城池", "宫殿",
    "房屋", "街道", "广场", "庭院", "房间", "大厅", "走廊", "地下室",
    "阳光", "月光", "星光", "灯光", "烛光", "光辉", "阴影", "昏暗",
    "明亮", "漆黑", "金黄", "银白", "血红", "碧绿", "蔚蓝", "赤红",
    "风雨", "雷电", "雾气", "雪花", "寒风", "烈日", "暴雨", "乌云",
    "阴森", "肃穆", "喧嚣", "寂静", "压抑", "壮丽", "荒凉", "破败",
    "望去", "看去", "呈现", "映入眼帘", "景象", "景致", "风光",
    "气息", "味道", "声音", "回响", "弥漫", "笼罩", "覆盖",
    "黄昏", "黎明", "深夜", "清晨", "傍晚", "夜幕", "晨曦",
]


def extract_scenes(text, chapters):
    """三区比例采样场景描写"""
    early, mid, late = partition_paragraphs(text, chapters)
    all_matches = []
    for zone_name, paras in [("early", early), ("mid", mid), ("late", late)]:
        zone_matches = []
        for para in paras:
            if len(para) < MIN_PASSAGE_LEN or len(para) > MAX_PASSAGE_LEN:
                continue
            kw_count = sum(1 for kw in SCENE_KEYWORDS if kw in para)
            if kw_count >= 2:
                zone_matches.append({
                    "text": para,
                    "keywords": [kw for kw in SCENE_KEYWORDS if kw in para],
                    "length": len(para),
                    "zone": zone_name,
                })
        all_matches.extend(proportional_sample(zone_matches, SAMPLES_PER_ZONE))

    return all_matches[:SAMPLES_PER_DIM]


# ── 维度2：内心活动 ─────────────────────────────────────

INNER_KEYWORDS = [
    "心想", "心中", "心里", "内心深处", "心底", "暗暗", "暗自",
    "默念", "心道", "思忖", "寻思", "暗想",
    "感到", "觉得", "感觉", "意识到", "明白", "醒悟", "领悟",
    "恐惧", "害怕", "紧张", "焦虑", "不安", "愤怒", "悲伤",
    "喜悦", "兴奋", "激动", "感动", "怀念", "思念", "愧疚",
    "不甘", "不服", "不愿", "不想", "不愿相信",
    "难道", "莫非", "或许", "也许", "大概", "恐怕",
    "必须", "一定", "绝不让", "发誓", "决心", "坚定",
]


def extract_psychology(text, chapters):
    """三区比例采样内心活动"""
    early, mid, late = partition_paragraphs(text, chapters)
    all_matches = []
    for zone_name, paras in [("early", early), ("mid", mid), ("late", late)]:
        zone_matches = []
        for para in paras:
            if len(para) < 40 or len(para) > 500:
                continue
            kw_count = sum(1 for kw in INNER_KEYWORDS if kw in para)
            if kw_count >= 2:
                zone_matches.append({
                    "text": para,
                    "keywords": [kw for kw in INNER_KEYWORDS if kw in para],
                    "length": len(para),
                    "zone": zone_name,
                })
        all_matches.extend(proportional_sample(zone_matches, SAMPLES_PER_ZONE))

    return all_matches[:SAMPLES_PER_DIM]


# ── 维度3：人物描写 ─────────────────────────────────────

CHARACTER_KEYWORDS = [
    "面容", "脸庞", "眉眼", "眼眸", "眼睛", "目光", "眼神", "嘴角",
    "鼻梁", "嘴唇", "发丝", "头发", "长发", "短发", "白发", "黑发",
    "身材", "身形", "身姿", "身影", "背影", "肩膀", "手臂", "手指",
    "皮肤", "肌肤", "脸色", "面色", "神色", "神情", "表情", "气质",
    "衣着", "穿着", "衣袍", "长袍", "战甲", "铠甲", "服饰", "装扮",
    "站着", "坐着", "躺着", "踱步", "背负", "双手", "负手而立",
    "目光如", "气势", "威压", "气场", "凌厉", "冷峻", "温和",
]


def extract_characters(text, chapters):
    """三区比例采样人物描写"""
    early, mid, late = partition_paragraphs(text, chapters)
    all_matches = []
    for zone_name, paras in [("early", early), ("mid", mid), ("late", late)]:
        zone_matches = []
        for para in paras:
            if len(para) < 50 or len(para) > 400:
                continue
            kw_count = sum(1 for kw in CHARACTER_KEYWORDS if kw in para)
            if kw_count >= 2:
                zone_matches.append({
                    "text": para,
                    "keywords": [kw for kw in CHARACTER_KEYWORDS if kw in para],
                    "length": len(para),
                    "zone": zone_name,
                })
        all_matches.extend(proportional_sample(zone_matches, SAMPLES_PER_ZONE))

    return all_matches[:SAMPLES_PER_DIM]


# ── 维度4：伏笔反转 v2 — 章节感知 + 跨章配对 ────────────

# 伏笔「设定」信号 — 前30%章节中检测
SETUP_PATTERNS = [
    # 预言/注定
    (r'(?:注定|命运|天命|预言|必将|终究会|迟早|总有一天|早晚)(.{20,200}?)(?:[。！？\n])', "prophecy"),
    # 神秘事物引入
    (r'(?:传说中|据说|传闻|上古|远古|太古|洪荒)(.{20,200}?)(?:[。！？\n])', "legend"),
    # 未解之谜
    (r'(?:奇怪.{0,10}的是|诡异.{0,10}的是|令人费解|不可思议|不明所以|百思不得其解)(.{30,200}?)(?:[。！？\n])', "mystery"),
    # 特殊物品/能力引入（"这是..." + 神秘描述）
    (r'(?:这是.{0,5}(?:一件|一枚|一把|一颗|一种).{0,10}(?:宝物|法宝|神器|丹药|功法|秘术|禁术))(.{20,200}?)(?:[。！？\n])', "artifact"),
    # 隐藏身份
    (r'(?:真实身份|真正身份|真实来历|真正来历|真实面目|不简单|深藏不露|隐藏.{0,5}(?:实力|修为))(.{20,200}?)(?:[。！？\n])', "hidden_identity"),
    # 伏笔式暗示
    (r'(?:日后|后来才|许久之后|多年后|很久以后.{0,5}才)(.{20,200}?)(?:[。！？\n])', "hint"),
    # 异常/矛盾
    (r'(?:不对劲|不对.{0,5}啊|怎么会.{0,5}呢|按理说.{0,10}但|本来应该.{0,10}却)(.{30,200}?)(?:[。！？\n])', "anomaly"),
]

# 伏笔「回收」信号 — 后70%章节中检测
PAYOFF_PATTERNS = [
    # 揭露真相
    (r'(?:原来|竟然是|居然是|竟是|真相.{0,5}(?:是|为|乃|在于)|终于明白|恍然大悟|豁然开朗|这才明白|此刻才知)(.{30,300}?)(?:[。！？\n])', "reveal"),
    # 反转
    (r'(?:谁料|不料|殊不知|却不知|哪知|岂料|万万没想到|做梦也没想到)(.{30,300}?)(?:[。！？\n])', "twist"),
    # 身份揭露
    (r'(?:真实身份.{0,5}(?:竟是|是|乃)|真正.{0,5}目的.{0,5}(?:竟是|是|乃)|原来.{1,10}(?:就是|才是|便是))(.{30,300}?)(?:[。！？\n])', "identity_reveal"),
    # 因果揭示
    (r'(?:之所以.{0,20}是因为|正是因为|原因.{0,5}(?:是|在于|竟是)|难怪.{0,10}原来)(.{30,300}?)(?:[。！？\n])', "causality"),
    # 回收前文伏笔式的陈述
    (r'(?:当初.{0,5}那|还记得.{0,5}那|曾经.{0,5}那|早在那时|很早以前.{0,5}就)(.{30,300}?)(?:[。！？\n])', "callback"),
    # 颠覆认知
    (r'(?:颠覆|推翻.{0,5}认知|完全不是.{0,5}想的那样|根本不是.{0,5}那样|一直以来.{0,5}都错了)(.{30,300}?)(?:[。！？\n])', "subversion"),
]

# 可用于配对的实体提取
ENTITY_PATTERNS = [
    # 人名（中文两字以上 + 称号）
    re.compile(r'(?:[秦韩赵魏楚燕齐刘陈李张王杨周吴郑冯蒋沈朱马何吕孔曹严华金魏陶华戚谢邹喻柏水窦章苏潘葛范彭鲁韦昌马苗凤花方俞任柳酆鲍史唐费廉岑薛雷贺倪汤殷罗毕郝邬安乐于时傅皮下齐康伍余元卜顾]'
               r'[^\s，。！？、；：""''《》（）\n]{1,3})'),
    # 法宝/功法名（书名号内）
    re.compile(r'《([^》]{1,15})》'),
    # 特殊称号
    re.compile(r'(?:天尊|魔尊|仙尊|妖皇|鬼王|剑圣|刀皇|丹帝|阵帝|符皇|蛊神|蛊仙|蛊师|蛊王|春秋蝉|逆流河|智慧蛊|力量蛊|定仙游|荡魂山|落魄谷|疯魔窟)[^\s，。！？、]?'),
    # 地名
    re.compile(r'(?:[东南西北中]域|[东南西北中]荒|[东南西北中]洲|大陆|山脉|古城|秘境|禁区)[^\s，。！？、]*'),
]

# 强关联词（连接设定和回收的语义桥）
BRIDGE_KEYWORDS = [
    "原来", "竟然", "真相", "身份", "秘密", "隐藏", "真正",
    "前世", "今生", "轮回", "转世", "重生", "因果", "命运",
    "注定", "预言", "天机", "命数", "劫数", "机缘", "造化",
    "传承", "血脉", "天赋", "觉醒", "封印", "解开", "开启",
]


def extract_entities(text_snippet):
    """从文本片段中提取实体名称"""
    entities = set()
    for pat in ENTITY_PATTERNS:
        for m in pat.finditer(text_snippet):
            entity = m.group(0).strip()
            if 2 <= len(entity) <= 15:
                entities.add(entity)
    return entities


def extract_chapter_setups(chapters_text_list):
    """从前30%章节捕获伏笔设定 + 实体"""
    setups = []
    for ch_idx, ch_text in enumerate(chapters_text_list):
        ch_entities = extract_entities(ch_text)
        for pattern, stype in SETUP_PATTERNS:
            for m in re.finditer(pattern, ch_text):
                match_text = m.group(0).strip()
                if len(match_text) < 30 or len(match_text) > 800:
                    continue
                # 提取匹配中的实体
                match_entities = extract_entities(match_text) | ch_entities
                setups.append({
                    "text": match_text,
                    "type": stype,
                    "chapter": ch_idx,
                    "entities": list(match_entities)[:10],
                    "length": len(match_text),
                })
    return setups


def extract_chapter_payoffs(chapters_text_list, chapter_offset=0):
    """从后70%章节捕获回收信号"""
    payoffs = []
    for ch_idx, ch_text in enumerate(chapters_text_list):
        actual_ch = ch_idx + chapter_offset
        for pattern, ptype in PAYOFF_PATTERNS:
            for m in re.finditer(pattern, ch_text):
                match_text = m.group(0).strip()
                if len(match_text) < 30 or len(match_text) > 800:
                    continue
                match_entities = extract_entities(match_text)
                payoffs.append({
                    "text": match_text,
                    "type": ptype,
                    "chapter": actual_ch,
                    "entities": list(match_entities)[:10],
                    "length": len(match_text),
                })
    return payoffs


def score_pairing(setup, payoff, total_chapters):
    """给设定-回收配对打分 (0-100)"""
    score = 0

    # 1. 实体重叠 (0-40)
    s_entities = set(setup.get("entities", []))
    p_entities = set(payoff.get("entities", []))
    overlap = s_entities & p_entities
    if overlap:
        score += min(40, len(overlap) * 15)

    # 2. 桥接词重叠 (0-25)
    s_words = {kw for kw in BRIDGE_KEYWORDS if kw in setup["text"]}
    p_words = {kw for kw in BRIDGE_KEYWORDS if kw in payoff["text"]}
    bridge_overlap = s_words & p_words
    if bridge_overlap:
        score += min(25, len(bridge_overlap) * 8)

    # 3. 章节距离奖励 (0-20) — 跨章越远越好（真伏笔）
    ch_distance = payoff["chapter"] - setup["chapter"]
    if ch_distance > 0:
        # 最佳距离：总章数的 10%-70%
        ideal_min = total_chapters * 0.1
        ideal_max = total_chapters * 0.7
        if ideal_min <= ch_distance <= ideal_max:
            score += 20
        elif ch_distance > ideal_max:
            score += 10  # 太远也有可能是回收
        else:
            score += 5   # 太近，可能是连续叙述

    # 4. 类型兼容性 (0-15)
    type_pairs = {
        ("prophecy", "reveal"): 15,
        ("prophecy", "causality"): 12,
        ("mystery", "reveal"): 15,
        ("mystery", "causality"): 10,
        ("hidden_identity", "identity_reveal"): 15,
        ("hidden_identity", "reveal"): 13,
        ("artifact", "reveal"): 12,
        ("artifact", "callback"): 12,
        ("legend", "reveal"): 10,
        ("hint", "callback"): 15,
        ("hint", "reveal"): 12,
        ("anomaly", "subversion"): 15,
        ("anomaly", "causality"): 12,
    }
    pair_key = (setup["type"], payoff["type"])
    score += type_pairs.get(pair_key, 5)

    return score


def extract_twists_v2(text, chapters):
    """章节感知伏笔分析：设定→回收→配对打分"""
    if not chapters:
        # 退化模式：用 v1 正则（兼容无章小说）
        return extract_twists_v1(text), []

    n_chapters = len(chapters)

    # 收集所有章节文本
    all_ch_texts = []
    for ci in range(n_chapters):
        all_ch_texts.append(get_chapter_text(text, chapters, ci))

    # Phase 1: 前30%章捕获设定
    early_cut = max(1, n_chapters // 3)
    early_texts = all_ch_texts[:early_cut]
    setups = extract_chapter_setups(early_texts)
    print(f"  🔍 捕获伏笔设定: {len(setups)} 条", file=sys.stderr)

    # Phase 2: 后70%章捕获回收
    later_texts = all_ch_texts[early_cut:]
    payoffs = extract_chapter_payoffs(later_texts, chapter_offset=early_cut)
    print(f"  🎯 捕获回收信号: {len(payoffs)} 条", file=sys.stderr)

    # Phase 3: 配对打分
    pairs = []
    for setup in setups:
        best_score = 0
        best_payoff = None
        for payoff in payoffs:
            s = score_pairing(setup, payoff, n_chapters)
            if s > best_score:
                best_score = s
                best_payoff = payoff
        if best_payoff and best_score >= 20:  # 最低20分才保留
            pairs.append({
                "setup": {
                    "text": setup["text"],
                    "type": setup["type"],
                    "chapter": setup["chapter"],
                },
                "payoff": {
                    "text": best_payoff["text"],
                    "type": best_payoff["type"],
                    "chapter": best_payoff["chapter"],
                },
                "score": best_score,
                "distance": best_payoff["chapter"] - setup["chapter"],
            })

    # 打分排序
    pairs.sort(key=lambda p: p["score"], reverse=True)
    pairs = pairs[:TWIST_PAIRS_MAX]
    print(f"  🔗 配对成功: {len(pairs)} 对 (阈值≥20分)", file=sys.stderr)

    # 同时保留 v1 单句模式（兼容）
    singles = extract_twists_v1(text)

    return singles, pairs


def extract_twists_v1(text):
    """v1 正则匹配（保留兼容）"""
    TWIST_PATTERNS = [
        (r'(?:原来|其实|竟然|没想到|谁料|不料|殊不知|却不知)(.{30,200}?)(?:[。！？\n])', "reveal"),
        (r'(?:但是|然而|可是|只是)(.{30,200}?)(?:[。！？\n])', "twist"),
        (r'(?:真实身份|真正目的|原来如此|真相)(.{20,200}?)(?:[。！？\n])', "expose"),
        (r'(?:后来才|日后|许久之后|多年后.{0,10}才)(.{20,200}?)(?:[。！？\n])', "foreshadow"),
        (r'(?:忽然|突然|猛然|一瞬间|刹那间)(.{30,150}?)(?:[。！？\n])', "sudden"),
    ]

    twists = []
    for pattern, twist_type in TWIST_PATTERNS:
        matches = re.findall(pattern, text)
        for match in matches[:10]:
            match = match.strip()
            if MIN_PASSAGE_LEN <= len(match) <= MAX_PASSAGE_LEN:
                twists.append({
                    "text": match,
                    "type": twist_type,
                    "length": len(match),
                })

    # 去重
    seen = set()
    unique = []
    for t in twists:
        key = t["text"][:40]
        if key not in seen:
            seen.add(key)
            unique.append(t)

    return unique[:40]


# ── 主流程 ──────────────────────────────────────────────

def analyze_novel(novel_title, novel_dir, output_dir):
    """分析单本小说，提炼四大维度精华。"""
    novel_file = novel_dir / "novel.txt"
    if not novel_file.exists():
        return {"error": f"全文文件不存在: {novel_file}"}

    print(f"📖 分析: 《{novel_title}》", file=sys.stderr)

    # 自动检测编码
    raw = novel_file.read_bytes()
    text = None
    for enc in ['utf-8', 'gb18030', 'gbk', 'gb2312']:
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode('utf-8', errors='replace')

    print(f"  总字数: {len(text):,}", file=sys.stderr)

    # 章节检测
    chapters = detect_chapters(text)
    print(f"  检测到 {len(chapters)} 章", file=sys.stderr)

    # 提取四大维度
    scenes = extract_scenes(text, chapters)
    print(f"  🏞️ 场景描写: {len(scenes)} 段 (前中后比例采样)", file=sys.stderr)

    psychology = extract_psychology(text, chapters)
    print(f"  💭 内心活动: {len(psychology)} 段 (前中后比例采样)", file=sys.stderr)

    characters = extract_characters(text, chapters)
    print(f"  👤 人物描写: {len(characters)} 段 (前中后比例采样)", file=sys.stderr)

    # v2 伏笔分析（章节感知 + 跨章配对）
    twists, foreshadowing_pairs = extract_twists_v2(text, chapters)
    print(f"  🎭 伏笔单句: {len(twists)} 条 | 跨章配对: {len(foreshadowing_pairs)} 对", file=sys.stderr)

    # 统计各区分布
    zone_stats = {}
    for dim_name, samples in [("scenes", scenes), ("psychology", psychology), ("characters", characters)]:
        zones = Counter(s.get("zone", "unknown") for s in samples)
        zone_stats[dim_name] = dict(zones)

    # 保存
    out_dir = output_dir / novel_title
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "title": novel_title,
        "total_chars": len(text),
        "chapters": len(chapters),
        "scenes": {"count": len(scenes), "samples": scenes[:20], "zone_distribution": zone_stats.get("scenes", {})},
        "psychology": {"count": len(psychology), "samples": psychology[:20], "zone_distribution": zone_stats.get("psychology", {})},
        "characters": {"count": len(characters), "samples": characters[:20], "zone_distribution": zone_stats.get("characters", {})},
        "twists": {"count": len(twists), "samples": twists[:20]},
        "foreshadowing_pairs": {
            "count": len(foreshadowing_pairs),
            "pairs": foreshadowing_pairs[:15],  # 最前面15对
        },
    }

    # 分别保存
    for key in ["scenes", "psychology", "characters", "twists", "foreshadowing_pairs"]:
        filepath = out_dir / f"{key}.json"
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(result[key], f, ensure_ascii=False, indent=2)

    # 汇总
    summary_path = out_dir / "summary.json"
    with open(summary_path, 'w', encoding='utf-8') as f:
        summary = {
            "title": novel_title,
            "total_chars": result["total_chars"],
            "chapters": len(chapters),
            "scenes": len(scenes),
            "psychology": len(psychology),
            "characters": len(characters),
            "twists": len(twists),
            "foreshadowing_pairs": len(foreshadowing_pairs),
            "zone_coverage": zone_stats,
        }
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return {
        "title": novel_title,
        "total_chars": len(text),
        "chapters": len(chapters),
        "scenes": len(scenes),
        "psychology": len(psychology),
        "characters": len(characters),
        "twists": len(twists),
        "foreshadowing_pairs": len(foreshadowing_pairs),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: analyzer.py <novel_name>", file=sys.stderr)
        print("       analyzer.py --all", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--all":
        results = []
        for novel_dir in sorted(NOVEL_DIR.iterdir()):
            if novel_dir.is_dir() and (novel_dir / "novel.txt").exists():
                result = analyze_novel(novel_dir.name, novel_dir, OUTPUT_DIR)
                results.append(result)
                print(json.dumps(result, ensure_ascii=False))
        print(f"\n📊 共分析 {len(results)} 本", file=sys.stderr)
    else:
        novel_name = sys.argv[1]
        novel_dir = NOVEL_DIR / novel_name
        result = analyze_novel(novel_name, novel_dir, OUTPUT_DIR)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
