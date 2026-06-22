#!/usr/bin/env python3
"""
网文精华锚点匹配分析器 v4 — LLM四维驱动

核心变化：每个锚点由 LLM 预生成四维预期内容（场景/内心/人物/伏笔），
匹配器对每个维度的每条预期内容独立进行关键词密度搜索，
从而找到小说中真正对应维度的原文段落。

v3: 锚点描述 → 关键词密度 → 模糊段落 → 盲扫四维
v4: 锚点 + 4维预期 → 分维关键词密度 → 精确维度原文
"""

import json
import os
import re
import sys
from pathlib import Path
from difflib import SequenceMatcher
from collections import Counter, defaultdict

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import NOVEL_ACQUISITION_DIR

# ── Config ──────────────────────────────────────────────
NOVEL_DIR = NOVEL_ACQUISITION_DIR / "novels"
OUTPUT_DIR = NOVEL_ACQUISITION_DIR / "extracted"
ANCHOR_DIR = NOVEL_ACQUISITION_DIR / "anchors"
TOP_N_PER_DIM = 2  # 每个维度样本取 top-N 匹配段

# ── 关键词提取 ──────────────────────────────────────────

def extract_keywords(text, min_len=2, max_len=8, top_k=25):
    """从文本提取特征关键词"""
    stop_chars = set('的是在了不和有我这他中就也们来去说看要能把让被对从到过')
    stop_words = {
        '一个', '一种', '这个', '那个', '什么', '怎么', '为什么',
        '只是', '不过', '但是', '还是', '已经', '因为', '所以',
        '可以', '没有', '不是', '自己', '他们', '我们',
        '忽然', '原来', '竟然', '其实', '终于',
    }
    
    words = []
    for l in range(min_len, max_len + 1):
        for i in range(len(text) - l + 1):
            w = text[i:i+l]
            if w in stop_words:
                continue
            if len(w) >= 3:
                words.append(w)
            elif len(w) == 2:
                if not all(c in stop_chars for c in w):
                    words.append(w)
    
    freq = Counter(words)
    return [w for w, _ in freq.most_common(top_k)]


# ── 段落分词 ────────────────────────────────────────────

def split_paragraphs(text, min_len=50):
    """将全文切分为段落"""
    return [p.strip() for p in re.split(r'\n+', text) if len(p.strip()) >= min_len]


# ── 关键词密度搜索 ──────────────────────────────────────

def keyword_search(paragraphs, query_text, top_n=TOP_N_PER_DIM, min_hits=2):
    """在段落列表中按关键词密度搜索"""
    keywords = extract_keywords(query_text)
    if not keywords:
        return []
    
    scored = []
    for pi, para in enumerate(paragraphs):
        hits = sum(1 for kw in keywords if kw in para)
        if hits >= min_hits:
            density = hits / max(1, len(para) / 100)
            scored.append((pi, para, hits, density))
    
    scored.sort(key=lambda x: x[3], reverse=True)
    
    results = []
    for pi, para, hits, density in scored[:top_n]:
        matched_kw = [kw for kw in keywords if kw in para]
        # 扩展上下文
        ctx_start = max(0, pi - 2)
        ctx_end = min(len(paragraphs), pi + 3)
        context = '\n'.join(paragraphs[ctx_start:ctx_end])
        
        results.append({
            "paragraph_index": pi,
            "text": para[:400],
            "hits": hits,
            "keyword_density": round(density, 2),
            "matched_keywords": matched_kw[:12],
            "context": context[:600],
        })
    
    return results


# ── v4 分维匹配 ─────────────────────────────────────────

def match_anchor_v4(paragraphs, anchor):
    """
    v4 匹配：scenes/psychology/characters 用关键词密度搜索；
    twists/intelligence 不搜索——直接收录 LLM 分析结果。
    """
    label = anchor.get("label", "")
    dimensions = anchor.get("dimensions", {})
    
    dim_results = {}
    
    # 场景/内心/人物：关键词密度匹配
    for dim_name in ["scenes", "psychology", "characters"]:
        samples = dimensions.get(dim_name, [])
        dim_matches = []
        for sample in samples:
            if isinstance(sample, str):
                matches = keyword_search(paragraphs, sample, top_n=TOP_N_PER_DIM)
                dim_matches.extend(matches)
        
        # 去重 + 按密度排序
        seen = set()
        unique = []
        for m in sorted(dim_matches, key=lambda x: x["keyword_density"], reverse=True):
            key = m["text"][:50]
            if key not in seen:
                seen.add(key)
                unique.append(m)
        
        dim_results[dim_name] = unique[:TOP_N_PER_DIM * 2]
    
    # 伏笔/反转 + 智商/智力：LLM 分析直接收录（不搜索正文）
    for llm_dim in ["twists", "intelligence"]:
        raw = dimensions.get(llm_dim, [])
        if raw:
            if isinstance(raw, list) and all(isinstance(t, str) for t in raw):
                dim_results[llm_dim] = [{"llm_analysis": t} for t in raw]
            elif isinstance(raw, list):
                dim_results[llm_dim] = [
                    {"llm_analysis": t if isinstance(t, str) else str(t)} 
                    for t in raw
                ]
    
    return dim_results


# ── 主流程 ──────────────────────────────────────────────

def analyze_novel_v4(novel_title, novel_dir, output_dir):
    """v4 分析：加载锚点 → 分词 → 分维匹配 → 输出"""
    novel_file = novel_dir / "novel.txt"
    if not novel_file.exists():
        return {"error": f"全文文件不存在: {novel_file}"}
    
    print(f"📖 v4 锚点分析: 《{novel_title}》", file=sys.stderr)
    
    # 加载全文
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
    
    # 分词
    paragraphs = split_paragraphs(text)
    print(f"  段落数: {len(paragraphs):,}", file=sys.stderr)
    
    # 加载锚点
    anchor_file = ANCHOR_DIR / f"{novel_title}.json"
    if not anchor_file.exists():
        return {"error": f"锚点文件不存在"}
    
    with open(anchor_file, 'r', encoding='utf-8') as f:
        anchors = json.load(f)
    
    print(f"  🎯 锚点: {len(anchors)}", file=sys.stderr)
    
    # 逐锚点分维匹配
    results = []
    dim_counts = defaultdict(int)
    
    for anchor in anchors:
        label = anchor.get("label", "")
        category = anchor.get("category", "")
        has_v4 = bool(anchor.get("dimensions"))
        
        if has_v4:
            dim_results = match_anchor_v4(paragraphs, anchor)
        else:
            # v3 兼容：用 quote 搜全部维度
            quote = anchor.get("quote", "")
            matches = keyword_search(paragraphs, quote, top_n=3)
            dim_results = {"general": matches}
        
        # 统计
        total_matches = sum(len(v) for v in dim_results.values())
        for dim, matches in dim_results.items():
            if matches:
                dim_counts[dim] += 1
        
        results.append({
            "anchor": label,
            "category": category,
            "v4_enabled": has_v4,
            "total_matches": total_matches,
            "dimensions": {k: v[:3] for k, v in dim_results.items()},
        })
        
        status = "v4" if has_v4 else "v3"
        dim_str = " | ".join(f"{d}×{len(m)}" for d, m in dim_results.items() if m)
        print(f"  [{status}] {label}: {dim_str or '⚠️ 无匹配'}", file=sys.stderr)
    
    # 汇总
    total_dim_matches = sum(dim_counts.values())
    summary = {
        "title": novel_title,
        "total_chars": len(text),
        "total_paragraphs": len(paragraphs),
        "total_anchors": len(anchors),
        "v4_anchors": sum(1 for a in anchors if a.get("dimensions")),
        "dimension_coverage": dict(dim_counts),
        "total_dimension_matches": total_dim_matches,
        "results": results,
    }
    
    # 保存
    out_dir = output_dir / novel_title
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / "anchor_analysis.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    print(f"  💾 {output_file}", file=sys.stderr)
    return summary


# ── CLI ──────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: anchor-matcher.py <novel_name>", file=sys.stderr)
        print("       anchor-matcher.py --all", file=sys.stderr)
        sys.exit(1)
    
    if sys.argv[1] == "--all":
        results = []
        for novel_dir in sorted(NOVEL_DIR.iterdir()):
            if novel_dir.is_dir() and (novel_dir / "novel.txt").exists():
                result = analyze_novel_v4(novel_dir.name, novel_dir, OUTPUT_DIR)
                if isinstance(result, dict):
                    results.append({
                        "title": result.get("title", novel_dir.name),
                        "anchors": result.get("total_anchors", 0),
                        "v4_anchors": result.get("v4_anchors", 0),
                        "dim_matches": result.get("total_dimension_matches", 0),
                    })
        
        print(f"\n📊 共完成 {len(results)} 本", file=sys.stderr)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        novel_name = sys.argv[1]
        novel_dir = NOVEL_DIR / novel_name
        result = analyze_novel_v4(novel_name, novel_dir, OUTPUT_DIR)
        print(json.dumps({
            "title": result.get("title"),
            "anchors": result.get("total_anchors"),
            "v4_anchors": result.get("v4_anchors"),
            "dimensions": result.get("dimension_coverage"),
            "total_matches": result.get("total_dimension_matches"),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
