#!/usr/bin/env python3
"""
网文学习编排器 — 逐本：搜索→下载→分析→通知→下一本
每次运行处理一本小说，更新进度状态。
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

# ── Paths ───────────────────────────────────────────────
for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import NOVEL_ACQUISITION_DIR, REFERENCE_NOVELS_DIR

BASE_DIR = NOVEL_ACQUISITION_DIR
NOVEL_LIST = BASE_DIR / "novel_list.json"
STATE_FILE = BASE_DIR / "orchestrator_state.json"
DOWNLOADER = BASE_DIR / "downloader.py"
ANALYZER = BASE_DIR / "analyzer.py"
NOVELS_DIR = BASE_DIR / "novels"
REF_NOVELS_DIR = REFERENCE_NOVELS_DIR

# 确保以正确目录运行
os.chdir(str(BASE_DIR))


# ── 已有小说检测 ────────────────────────────────────────

def scan_existing_references():
    """扫描参考小说目录，返回已知标题集合。"""
    existing = set()
    if not REF_NOVELS_DIR.exists():
        return existing

    for f in REF_NOVELS_DIR.iterdir():
        name = f.stem  # 去掉扩展名
        # 解析格式：《书名》[作者] 或 书名.txt
        # 提取书名
        title_match = re.search(r'《([^》]+)》', name)
        if title_match:
            existing.add(title_match.group(1))
        else:
            # 无书名号，直接当文件名
            clean = re.sub(r'[\[（(].*', '', name).strip()
            if clean:
                existing.add(clean)
    return existing


# ── State ───────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"completed": [], "current": None, "total_processed": 0, "history": []}


def save_state(state):
    state["updated_at"] = datetime.now().isoformat()
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_novel_list():
    with open(NOVEL_LIST, 'r') as f:
        return json.load(f)["novels"]


# ── Steps ───────────────────────────────────────────────

def step_download(title, author):
    """Step 1: Download novel."""
    print(f"\n{'='*60}")
    print(f"📥 第一步：下载 《{title}》")
    print(f"{'='*60}")

    result = subprocess.run(
        ["python3", str(DOWNLOADER), str(NOVEL_LIST), "--single", title],
        capture_output=True, text=True, timeout=3600
    )

    for line in result.stdout.strip().split("\n"):
        try:
            data = json.loads(line)
            return data
        except:
            pass

    return {"title": title, "success": False, "chapters": 0, "error": result.stderr[-200:]}


def step_analyze(title):
    """Step 2: Analyze novel."""
    print(f"\n{'='*60}")
    print(f"🔬 第二步：分析 《{title}》")
    print(f"{'='*60}")

    result = subprocess.run(
        ["python3", str(ANALYZER), title],
        capture_output=True, text=True, timeout=600
    )

    try:
        return json.loads(result.stdout.strip().split("\n")[-1])
    except:
        return {"title": title, "error": result.stderr[-200:]}


def format_notification(title, author, tags, dl_result, analysis):
    """Format WeChat notification message."""
    now = datetime.now().strftime("%m-%d %H:%M")

    lines = [
        f"📚 网文学习 · 完成一本",
        f"",
        f"**《{title}》** — {author}",
        f"类型：{', '.join(tags[:3])}",
        f"",
    ]

    if dl_result.get("success"):
        lines.append(f"✅ 下载：{dl_result.get('chapters', 0)} 章")
    else:
        lines.append(f"❌ 下载失败：{dl_result.get('error', '未知')}")

    if "scenes" in analysis:
        lines.append(f"✅ 分析完成：")
        lines.append(f"  🏞️ 场景描写 {analysis['scenes']} 段")
        lines.append(f"  💭 内心活动 {analysis['psychology']} 段")
        lines.append(f"  👤 人物描写 {analysis['characters']} 段")
        lines.append(f"  🎭 伏笔反转 {analysis.get('twists', 0)} 条")
        if analysis.get('foreshadowing_pairs'):
            lines.append(f"  🔗 跨章伏笔配对 {analysis['foreshadowing_pairs']} 对")
    elif "error" in analysis:
        lines.append(f"⚠️ 分析异常：{analysis['error'][:80]}")

    lines.append(f"")
    lines.append(f"⏰ {now}")

    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────

def main():
    novels = load_novel_list()
    state = load_state()
    completed_titles = {c["title"] for c in state["completed"]}
    existing_refs = scan_existing_references()

    # 标记已存在的参考小说（跳过不重复下载）
    for novel in novels:
        if novel["title"] in existing_refs and novel["title"] not in completed_titles:
            state["completed"].append({
                "title": novel["title"],
                "author": novel["author"],
                "score": novel.get("score", "?"),
                "processed_at": datetime.now().isoformat(),
                "status": "skipped_existing",
                "reason": "参考小说目录已有",
            })
            completed_titles.add(novel["title"])
            print(f"⏭️ 跳过(已有): 《{novel['title']}》", file=sys.stderr)

    if len(state["completed"]) > state.get("total_processed", 0):
        state["total_processed"] = len(state["completed"])
        save_state(state)

    # 找下一本待处理
    next_novel = None
    for novel in novels:
        if novel["title"] not in completed_titles:
            next_novel = novel
            break

    if not next_novel:
        print("🎉 所有小说已处理完毕！")
        print(json.dumps({"status": "all_done", "total": len(completed_titles)}, ensure_ascii=False))
        return

    title = next_novel["title"]
    author = next_novel["author"]
    tags = next_novel.get("tags", [])
    score = next_novel.get("score", "?")

    print(f"📖 开始处理: 《{title}》({author}) ⭐{score}  [{len(completed_titles)+1}/{len(novels)}]")

    # Step 1: 下载
    dl_result = step_download(title, author)
    print(json.dumps(dl_result, ensure_ascii=False))

    # Step 2: 分析
    analysis = {}
    if dl_result.get("success"):
        analysis = step_analyze(title)
        print(json.dumps(analysis, ensure_ascii=False))

    # 记录结果
    record = {
        "title": title,
        "author": author,
        "score": score,
        "processed_at": datetime.now().isoformat(),
        "download": dl_result,
        "analysis": analysis,
    }
    state["completed"].append(record)
    state["total_processed"] = len(state["completed"])
    state["current"] = title
    save_state(state)

    # 输出通知（stdout → cron 投递到微信）
    notification = format_notification(title, author, tags, dl_result, analysis)
    print(f"\n{'='*60}")
    print("NOTIFICATION:")
    print(notification)

    # 输出 JSON 摘要
    summary = {
        "title": title,
        "author": author,
        "progress": f"{len(state['completed'])}/{len(novels)}",
        "download_ok": dl_result.get("success", False),
        "chapters": dl_result.get("chapters", 0),
        "analysis": {
            k: v for k, v in analysis.items()
            if k in ["scenes", "psychology", "characters", "twists", "foreshadowing_pairs"]
        } if analysis else {},
        "next": None,
    }

    # 找下一本
    for novel in novels:
        if novel["title"] not in {c["title"] for c in state["completed"]}:
            summary["next"] = f"{novel['title']} ({novel['author']}) ⭐{novel.get('score','?')}"
            break

    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
