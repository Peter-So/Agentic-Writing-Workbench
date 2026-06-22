#!/usr/bin/env python3
"""
五维资料库语义搜索 v1.0 — 模糊匹配，跨题材通用

核心思路：不限题材，用TF-IDF+余弦相似度找最相关的段落。
"饭堂"能匹配到"客栈/酒楼/宴席"，因为它们共享进食/社交/聚集的语义场。

用法：
    python3 semantic-search.py "在饭堂吃饭被人注视"
    python3 semantic-search.py "少年被混混欺负后沉默" --top 10
    python3 semantic-search.py "师生关系，老师关心贫困学生" --dim psychology
    python3 semantic-search.py --build   # 重建索引（锚点更新后执行）

索引缓存在当前 writing 项目的 novel-acquisition/cache/tfidf_index.pkl
首次构建约30秒，之后搜索<1秒。
"""

import json
import sys
import os
import re
import argparse
import pickle
import time
import numpy as np
from pathlib import Path

# Force unbuffered UTF-8 output for Windows consoles.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

# ── Paths ───────────────────────────────────────────────
for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import NOVEL_ACQUISITION_DIR

BASE_DIR = NOVEL_ACQUISITION_DIR
EXTRACTED_DIR = BASE_DIR / "extracted"
CACHE_DIR = BASE_DIR / "cache"
INDEX_PATH = CACHE_DIR / "tfidf_index.pkl"

CACHE_DIR.mkdir(exist_ok=True)


# ── Index Building ──────────────────────────────────────

def load_all_segments():
    """加载全部段文本（含旧匹配结果+新锚点维度文本），返回 (texts, metadata)"""
    texts = []
    metadata = []
    
    # Source 1: 旧的anchor_analysis.json（已匹配到原文的段落）
    for novel_dir in sorted(EXTRACTED_DIR.iterdir()):
        if not novel_dir.is_dir():
            continue
        fp = novel_dir / "anchor_analysis.json"
        if not fp.exists():
            continue
        
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except:
            continue
        
        novel_name = novel_dir.name
        results = data.get("results", [])
        
        for anchor_info in results:
            anchor_label = anchor_info.get("anchor", anchor_info.get("anchor_label", ""))
            category = anchor_info.get("category", "")
            dims = anchor_info.get("dimensions", {})
            
            for dim_name, entries in dims.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict):
                        text = entry.get("text", "") or entry.get("llm_analysis", "")
                        context = entry.get("context", "")
                        full_text = (text + " " + context).strip()
                    elif isinstance(entry, str):
                        full_text = entry
                    else:
                        continue
                    
                    if len(full_text) < 10:
                        continue
                    
                    texts.append(full_text)
                    metadata.append({
                        "novel": novel_name,
                        "anchor": anchor_label,
                        "category": category,
                        "dimension": dim_name,
                        "text": full_text[:500],
                    })
    
    # Source 2: 新锚点文件 anchors/{novel}.json（LLM生成的维度样本文本）
    anchor_dir = BASE_DIR / "anchors"
    if anchor_dir.exists():
        for anchor_file in sorted(anchor_dir.glob("*.json")):
            novel_name = anchor_file.stem
            try:
                anchors = json.loads(anchor_file.read_text(encoding="utf-8"))
            except:
                continue
            
            if not isinstance(anchors, list):
                continue
            
            for anchor in anchors:
                if not isinstance(anchor, dict):
                    continue
                label = anchor.get("label", "")
                category = anchor.get("category", "")
                dims = anchor.get("dimensions", {})
                
                for dim_name, samples in dims.items():
                    if not isinstance(samples, list):
                        continue
                    for sample in samples:
                        if isinstance(sample, str) and len(sample) >= 10:
                            texts.append(sample)
                            metadata.append({
                                "novel": novel_name,
                                "anchor": label,
                                "category": category,
                                "dimension": dim_name,
                                "text": sample[:500],
                            })
    
    # Source 3: 项目产出降级语料 outputs-corpus/confirmed_outputs.json
    #   无 sidecar 时，用户确认的正文/摘要/设定落到这里，纳入 TF-IDF 重建供语义召回。
    fallback_file = BASE_DIR / "outputs-corpus" / "confirmed_outputs.json"
    if fallback_file.exists():
        try:
            records = json.loads(fallback_file.read_text(encoding="utf-8"))
        except Exception:
            records = []
        for rec in records if isinstance(records, list) else []:
            txt = (rec.get("text") or "").strip()
            if isinstance(txt, str) and len(txt) >= 10:
                meta = rec.get("meta") or {}
                texts.append(txt)
                metadata.append({
                    "novel": "本项目产出",
                    "anchor": f"第{meta.get('chapter', '?')}章",
                    "category": "output",
                    "dimension": meta.get("type", "产出"),
                    "text": txt[:500],
                })

    return texts, metadata


def build_index(force=False):
    """构建TF-IDF索引"""
    if INDEX_PATH.exists() and not force:
        print("索引已存在，使用 --build 强制重建")
        return load_index()
    
    print("📦 加载全部文本段...")
    texts, metadata = load_all_segments()
    print(f"   共 {len(texts)} 段文本")
    
    print("🔤 jieba分词中...")
    import jieba
    jieba.setLogLevel(20)  # suppress jieba logs
    
    # Tokenize all texts
    tokenized = []
    for i, t in enumerate(texts):
        words = " ".join(jieba.cut(t))
        tokenized.append(words)
        if (i + 1) % 2000 == 0:
            print(f"   已分词 {i+1}/{len(texts)}...")
    
    print("📊 构建TF-IDF矩阵...")
    from sklearn.feature_extraction.text import TfidfVectorizer
    
    vectorizer = TfidfVectorizer(
        max_features=50000,  # 词汇表大小
        min_df=2,            # 至少出现2次
        max_df=0.8,          # 最多80%文档出现
        sublinear_tf=True,   # 对数TF
        norm='l2',           # L2归一化（方便余弦计算）
    )
    
    tfidf_matrix = vectorizer.fit_transform(tokenized)
    print(f"   矩阵形状: {tfidf_matrix.shape} (文档×词汇)")
    
    # Save index
    index_data = {
        "vectorizer": vectorizer,
        "tfidf_matrix": tfidf_matrix,
        "metadata": metadata,
        "build_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_segments": len(texts),
    }
    
    with open(INDEX_PATH, 'wb') as f:
        pickle.dump(index_data, f)
    
    size_mb = INDEX_PATH.stat().st_size / 1024 / 1024
    print(f"💾 索引已保存: {INDEX_PATH} ({size_mb:.1f}MB)")
    
    return index_data


def load_index():
    """加载已有索引"""
    if not INDEX_PATH.exists():
        return build_index(force=True)
    
    with open(INDEX_PATH, 'rb') as f:
        return pickle.load(f)


# ── Search ──────────────────────────────────────────────

def semantic_search(query, index_data, top_n=10, dim_filter=None, novel_filter=None):
    """
    语义搜索：用TF-IDF余弦相似度找最相关的段落。
    跨题材通用——"饭堂"能匹配"客栈/酒楼"。
    """
    import jieba
    jieba.setLogLevel(20)
    
    vectorizer = index_data["vectorizer"]
    tfidf_matrix = index_data["tfidf_matrix"]
    metadata = index_data["metadata"]
    
    # Tokenize query
    query_tokens = " ".join(jieba.cut(query))
    query_vec = vectorizer.transform([query_tokens])
    
    # Cosine similarity (since vectors are L2-normalized, dot product = cosine)
    scores = (tfidf_matrix @ query_vec.T).toarray().flatten()
    
    # Apply filters
    if dim_filter or novel_filter:
        for i, meta in enumerate(metadata):
            if dim_filter and meta["dimension"] not in dim_filter:
                scores[i] = 0
            if novel_filter and novel_filter not in meta["novel"]:
                scores[i] = 0
    
    # Get top-N
    top_indices = np.argsort(scores)[::-1][:top_n * 3]  # Get extra for dedup
    
    results = []
    seen_texts = set()
    
    for idx in top_indices:
        if scores[idx] <= 0:
            break
        
        meta = metadata[idx]
        text_key = meta["text"][:100]
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)
        
        results.append({
            "score": float(scores[idx]),
            "novel": meta["novel"],
            "anchor": meta["anchor"],
            "dimension": meta["dimension"],
            "category": meta["category"],
            "text": meta["text"],
        })
        
        if len(results) >= top_n:
            break
    
    return results


# ── CLI ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="五维资料库语义搜索")
    parser.add_argument('query', nargs='?', help='搜索query（自然语言描述场景/情绪/人物）')
    parser.add_argument('--build', action='store_true', help='重建索引')
    parser.add_argument('--top', type=int, default=10, help='返回top N结果')
    parser.add_argument('--dim', type=str, help='限定维度(scenes,psychology,characters,twists)')
    parser.add_argument('--novel', type=str, help='限定小说')
    parser.add_argument('--json', action='store_true', help='JSON输出')
    parser.add_argument('--stats', action='store_true', help='显示索引统计')
    args = parser.parse_args()
    
    if args.build:
        build_index(force=True)
        return
    
    if args.stats:
        if INDEX_PATH.exists():
            index = load_index()
            print(f"索引统计:")
            print(f"  总段数: {index['total_segments']}")
            print(f"  构建时间: {index['build_time']}")
            print(f"  矩阵形状: {index['tfidf_matrix'].shape}")
            print(f"  文件大小: {INDEX_PATH.stat().st_size / 1024 / 1024:.1f}MB")
        else:
            print("索引不存在，请先 --build")
        return
    
    if not args.query:
        parser.print_help()
        return
    
    # Load or build index
    index = load_index()
    
    # Parse dimension filter
    dim_filter = None
    if args.dim:
        dim_filter = [d.strip() for d in args.dim.split(',')]
    
    # Search
    results = semantic_search(args.query, index, args.top, dim_filter, args.novel)
    
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"\n🔍 搜索: \"{args.query}\"")
        print(f"   返回 {len(results)} 条结果\n")
        for i, r in enumerate(results):
            score_bar = "█" * int(r['score'] * 50)
            print(f"  [{i+1}] {r['score']:.3f} {score_bar}")
            print(f"      📖 {r['novel']} | {r['dimension']} | {r['anchor']}")
            print(f"      {r['text'][:150]}")
            print()


if __name__ == "__main__":
    main()
