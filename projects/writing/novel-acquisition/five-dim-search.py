#!/usr/bin/env python3
"""
五维资料库一键检索脚本 v1.0
用法:
    python3 five-dim-search.py "关键词1,关键词2,关键词3" [--dim scenes,psychology,characters] [--top 10] [--novel 蛊真人]
    python3 five-dim-search.py --trait "开朗" [--dim all] [--top 15]

功能:
    1. 预加载全部51本小说的anchor_analysis.json到内存（~2MB，<1秒）
    2. 支持多关键词并行搜索（逗号分隔）
    3. 支持 --trait 模式：自动拆词（抽象→行为→感官三阶）
    4. 按匹配密度排序，输出top-N结果
    5. 一次调用完成，替代30-60次search_files

输出格式: JSON数组，每条含 novel/anchor/dimension/text/score/matched_keywords
"""

import json
import sys
import os
import re
import argparse
from pathlib import Path
from collections import defaultdict

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import NOVEL_ACQUISITION_DIR, REFERENCE_NOVELS_DIR

# ===== 拆词映射表（三阶：抽象→行为→感官）=====
TRAIT_SPLIT_MAP = {
    # 性格类
    "开朗": ["笑", "阳光", "眼睛亮", "活泼", "不服输", "少年气", "生机", "快活", "嘴角", "咧开"],
    "明亮": ["笑", "阳光", "眼睛亮", "活泼", "不服输", "少年气", "生机", "快活"],
    "聪明": ["观察", "记住", "第一", "成绩", "举一反三", "悟性", "过目不忘", "好奇", "机敏", "发现"],
    "好学": ["读书", "成绩", "用功", "刻苦", "认真", "笔记", "钻研", "背书", "题", "考试"],
    "善良": ["帮", "护", "不忍", "心软", "让", "给", "分", "扶", "送", "救"],
    "正义": ["出头", "帮", "护", "不平", "较真", "倔", "不忍", "站出去", "拦", "挡"],
    "勇敢": ["冲", "挡", "站出", "不退", "硬", "扛", "拦", "握拳", "挺", "迎"],
    "沉默": ["不说话", "低头", "躲", "不敢", "从此", "学会", "收起来", "闭嘴", "安静", "一声不吭"],
    "自卑": ["不敢", "躲", "低头", "被笑", "窘迫", "难堪", "脸红", "缩", "退", "不配"],
    "孤独": ["一个人", "没人", "独自", "空", "冷", "安静", "角落", "靠窗", "最后一排", "走了"],
    "愤怒": ["拳", "咬", "红", "吼", "砸", "踢", "摔", "颤", "握", "忍不住"],
    "恐惧": ["抖", "冷汗", "退", "僵", "不敢", "心跳", "发白", "瞪大", "吞咽", "腿软"],
    "悲伤": ["泪", "哭", "红了眼", "喉咙", "说不出", "沉默", "雨", "走了", "再也", "背影"],
    "温柔": ["轻", "慢", "柔", "拍", "摸", "握住", "低声", "小心", "放下", "等"],
    # 处境类
    "贫困": ["穷", "没钱", "旧衣", "鞋底", "打工", "特困", "补助", "饥饿", "补丁", "省钱", "磨穿", "褪色", "瘦", "冷", "冻红"],
    "富裕": ["桑塔纳", "轿车", "名牌", "零花钱", "家里", "别墅", "保姆", "出国", "钢琴", "补习班"],
    "离别": ["走", "背影", "回头", "车站", "月台", "挥手", "越来越小", "消失", "再见", "最后一眼"],
    "死亡": ["没了", "走了", "闭眼", "冰冷", "僵", "白布", "殡仪", "骨灰", "遗照", "最后"],
    "成长": ["第一次", "学会", "不再", "终于", "明白", "变了", "长大", "从那以后", "独自", "扛"],
    "背叛": ["骗", "假", "利用", "发现", "原来", "真相", "从没", "一直", "撕", "碎"],
    # 场景类
    "校园": ["教室", "操场", "食堂", "宿舍", "黑板", "课桌", "铃声", "放学", "早自习", "晚修"],
    "战斗": ["剑", "刀", "血", "杀", "挡", "闪", "冲", "痛", "伤", "倒"],
    "夜晚": ["月", "星", "灯", "暗", "影", "安静", "虫鸣", "凉", "窗", "睡不着"],
    "雨天": ["雨", "伞", "淋", "湿", "水", "泥", "滑", "冷", "雾", "模糊"],
    # 角色定位类（降级搜索）
    "底层少年": ["穷", "打工", "旧", "瘦", "省", "挤", "破", "冷", "饿", "走路"],
    "老师": ["讲台", "班主任", "批改", "教", "学生", "办公室", "年纪", "眼镜", "粉笔", "三十年"],
    "混混": ["勒索", "打", "退学", "街头", "纹身", "抽烟", "堵", "兄弟", "帮派", "怕"],
    "母亲": ["妈", "做饭", "等", "灯", "门口", "皱纹", "手粗", "头发白", "念叨", "舍不得"],
    "父亲": ["爸", "沉默", "背", "扛", "老了", "手", "烟", "酒", "弯腰", "月台"],
}

# ===== 数据加载 =====
EXTRACTED_DIR = NOVEL_ACQUISITION_DIR / "extracted"
REFERENCE_DIR = REFERENCE_NOVELS_DIR

_cache = {}

def load_all_analyses():
    """预加载全部anchor_analysis.json到内存"""
    if _cache:
        return _cache
    for d in sorted(EXTRACTED_DIR.iterdir()):
        fp = d / "anchor_analysis.json"
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                _cache[d.name] = data
            except Exception as e:
                print(f"⚠️ 跳过 {d.name}: {e}", file=sys.stderr)
    return _cache

# ===== 检索引擎 =====
def search_five_dimensions(keywords: list[str], dimensions: list[str] = None, 
                           top_n: int = 15, novel_filter: str = None) -> list[dict]:
    """
    在五维资料库中搜索关键词，返回排序后的匹配结果。
    
    keywords: 搜索关键词列表
    dimensions: 限定维度 ['scenes','psychology','characters','twists','intelligence']，None=全部
    top_n: 返回top N结果
    novel_filter: 限定某本小说
    """
    all_dims = ["scenes", "psychology", "characters", "twists", "intelligence"]
    search_dims = dimensions if dimensions else all_dims
    
    db = load_all_analyses()
    results = []
    
    for novel_name, data in db.items():
        if novel_filter and novel_filter not in novel_name:
            continue
        
        anchors = data.get("results", [])
        for anchor_info in anchors:
            anchor_label = anchor_info.get("anchor", "")
            dims = anchor_info.get("dimensions", {})
            
            for dim_name in search_dims:
                entries = dims.get(dim_name, [])
                for entry in entries:
                    # twists/intelligence 是 llm_analysis 格式
                    text = entry.get("text", "") or entry.get("llm_analysis", "")
                    context = entry.get("context", "")
                    full_text = text + " " + context
                    
                    # 计算匹配得分
                    matched = []
                    score = 0
                    for kw in keywords:
                        count = full_text.count(kw)
                        if count > 0:
                            matched.append(kw)
                            score += count
                    
                    if matched:
                        results.append({
                            "novel": novel_name,
                            "anchor": anchor_label,
                            "dimension": dim_name,
                            "text": text[:300],
                            "context": context[:200] if context else "",
                            "score": score,
                            "matched_keywords": matched,
                            "match_count": len(matched),
                        })
    
    # 按匹配关键词数 → 得分排序
    results.sort(key=lambda x: (x["match_count"], x["score"]), reverse=True)
    return results[:top_n]


def trait_search(trait: str, dimensions: list[str] = None, top_n: int = 15, 
                 novel_filter: str = None) -> dict:
    """
    拆词模式：自动将抽象特质拆为多关键词搜索。
    返回: {trait, split_keywords, results, total_matches}
    """
    # 查找拆词表
    keywords = TRAIT_SPLIT_MAP.get(trait, None)
    if keywords is None:
        # 未预设的特质，尝试单字拆分 + 原词
        keywords = [trait]
        if len(trait) >= 2:
            keywords.extend(list(trait))
    
    results = search_five_dimensions(keywords, dimensions, top_n, novel_filter)
    
    return {
        "trait": trait,
        "split_keywords": keywords,
        "keyword_count": len(keywords),
        "results": results,
        "total_matches": len(results),
    }


def multi_trait_search(traits: list[str], dimensions: list[str] = None, 
                       top_n: int = 10, novel_filter: str = None) -> list[dict]:
    """批量拆词搜索多个特质"""
    all_results = []
    for t in traits:
        r = trait_search(t, dimensions, top_n, novel_filter)
        all_results.append(r)
    return all_results


def search_reference_novels(keywords: list[str], max_results: int = 5) -> list[dict]:
    """在参考小说原文中搜索（五维库无匹配时的降级方案）"""
    results = []
    if not REFERENCE_DIR.exists():
        return results
    
    for txt_file in sorted(REFERENCE_DIR.rglob("*.txt")):
        try:
            content = txt_file.read_text(encoding="utf-8", errors="ignore")
        except:
            continue
        
        # 按段落搜索
        paragraphs = content.split("\n")
        for i, para in enumerate(paragraphs):
            if len(para) < 20:
                continue
            matched = [kw for kw in keywords if kw in para]
            if len(matched) >= 2:  # 至少匹配2个关键词
                results.append({
                    "novel": txt_file.stem,
                    "line": i + 1,
                    "text": para[:300],
                    "matched_keywords": matched,
                    "score": sum(para.count(kw) for kw in matched),
                })
    
    results.sort(key=lambda x: (len(x["matched_keywords"]), x["score"]), reverse=True)
    return results[:max_results]


# ===== 统计信息 =====
def get_db_stats() -> dict:
    """返回资料库统计"""
    db = load_all_analyses()
    total_novels = len(db)
    total_anchors = 0
    total_segments = 0
    dim_counts = defaultdict(int)
    
    for novel_name, data in db.items():
        anchors = data.get("results", [])
        total_anchors += len(anchors)
        for a in anchors:
            dims = a.get("dimensions", {})
            for dim_name, entries in dims.items():
                dim_counts[dim_name] += len(entries)
                total_segments += len(entries)
    
    return {
        "novels": total_novels,
        "anchors": total_anchors,
        "total_segments": total_segments,
        "dimensions": dict(dim_counts),
    }


# ===== CLI =====
def main():
    parser = argparse.ArgumentParser(description="五维资料库一键检索")
    parser.add_argument("keywords", nargs="?", help="逗号分隔的关键词")
    parser.add_argument("--trait", help="拆词模式：输入抽象特质自动拆词")
    parser.add_argument("--traits", help="批量拆词：逗号分隔多个特质")
    parser.add_argument("--dim", default="all", help="维度过滤：scenes,psychology,characters,twists,intelligence 或 all")
    parser.add_argument("--top", type=int, default=15, help="返回top N结果")
    parser.add_argument("--novel", help="限定某本小说")
    parser.add_argument("--stats", action="store_true", help="显示资料库统计")
    parser.add_argument("--fallback", action="store_true", help="同时搜索参考小说原文")
    parser.add_argument("--json", action="store_true", help="纯JSON输出（供execute_code解析）")
    
    args = parser.parse_args()
    
    dims = None if args.dim == "all" else args.dim.split(",")
    
    if args.stats:
        stats = get_db_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return
    
    if args.traits:
        trait_list = [t.strip() for t in args.traits.split(",")]
        results = multi_trait_search(trait_list, dims, args.top, args.novel)
        if args.json:
            print(json.dumps(results, ensure_ascii=False))
        else:
            for r in results:
                print(f"\n{'='*60}")
                print(f"🔍 特质「{r['trait']}」→ 拆词: {r['split_keywords']}")
                print(f"   命中 {r['total_matches']} 段")
                for i, m in enumerate(r["results"][:5], 1):
                    print(f"   {i}. [{m['novel']}·{m['anchor']}] {m['dimension']} (score:{m['score']})")
                    print(f"      关键词: {m['matched_keywords']}")
                    print(f"      → {m['text'][:120]}...")
        return
    
    if args.trait:
        result = trait_search(args.trait, dims, args.top, args.novel)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"🔍 特质「{result['trait']}」→ 拆词({result['keyword_count']}个): {result['split_keywords']}")
            print(f"   命中 {result['total_matches']} 段\n")
            for i, m in enumerate(result["results"], 1):
                print(f"{i}. [{m['novel']}·{m['anchor']}] {m['dimension']} (score:{m['score']}, 匹配:{m['matched_keywords']})")
                print(f"   {m['text'][:150]}")
                print()
            
            if args.fallback and result["total_matches"] < 3:
                print(f"\n⚠️ 五维库命中不足3段，降级搜索参考小说原文...")
                fallback = search_reference_novels(result["split_keywords"])
                for i, m in enumerate(fallback, 1):
                    print(f"   📖 {i}. [{m['novel']}] line:{m['line']} 匹配:{m['matched_keywords']}")
                    print(f"      {m['text'][:150]}")
        return
    
    if args.keywords:
        kw_list = [k.strip() for k in args.keywords.split(",")]
        results = search_five_dimensions(kw_list, dims, args.top, args.novel)
        if args.json:
            print(json.dumps(results, ensure_ascii=False))
        else:
            print(f"🔍 关键词: {kw_list} | 维度: {args.dim} | Top {args.top}")
            print(f"   命中 {len(results)} 段\n")
            for i, m in enumerate(results, 1):
                print(f"{i}. [{m['novel']}·{m['anchor']}] {m['dimension']} (score:{m['score']}, 匹配:{m['matched_keywords']})")
                print(f"   {m['text'][:150]}")
                print()
            
            if args.fallback and len(results) < 3:
                print(f"\n⚠️ 五维库命中不足3段，降级搜索参考小说原文...")
                fallback = search_reference_novels(kw_list)
                for i, m in enumerate(fallback, 1):
                    print(f"   📖 {i}. [{m['novel']}] line:{m['line']} 匹配:{m['matched_keywords']}")
                    print(f"      {m['text'][:150]}")
        return
    
    parser.print_help()


if __name__ == "__main__":
    main()
