#!/usr/bin/env python3
"""
锚点扩张器 v1.0 — 批量为51本小说生成20-30个锚点

策略：
1. 从每本小说采样关键片段（开头/中段/结尾各5000字 + 章节标题列表）
2. 调用LLM生成25个锚点（含4维预期文本）
3. 保存到 anchors/{novel}.json
4. 之后用 anchor-matcher.py 匹配原文

用法：
    python3 anchor-expander.py                    # 处理所有需要扩张的小说
    python3 anchor-expander.py --novel 余罪        # 只处理指定小说
    python3 anchor-expander.py --dry-run          # 只显示计划不执行
    python3 anchor-expander.py --workers 3        # 并发数（默认2）
    python3 anchor-expander.py --target 25        # 目标锚点数（默认25）
"""

import json
import os
import re
import sys
import time
import argparse
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import NOVEL_ACQUISITION_DIR, REFERENCE_NOVELS_DIR, env_file

# Force unbuffered UTF-8 output for Windows consoles.
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

# ── Config ──────────────────────────────────────────────
BASE_DIR = NOVEL_ACQUISITION_DIR
ANCHOR_DIR = BASE_DIR / "anchors"
REF_DIR = REFERENCE_NOVELS_DIR
EXTRACTED_DIR = BASE_DIR / "extracted"

# LLM API config - use DeepSeek v4-flash for speed/cost
API_URL = "https://api.deepseek.com/chat/completions"

TARGET_ANCHORS = 25  # 目标锚点数
SAMPLE_SIZE = 6000   # 每段采样字数
MAX_RETRIES = 3

# Skip novels that already have enough anchors
def already_done(title, target):
    """检查是否已完成"""
    anchor_file = ANCHOR_DIR / f"{title}.json"
    if anchor_file.exists():
        with open(anchor_file, 'r') as f:
            data = json.load(f)
        if isinstance(data, list) and len(data) >= target:
            return True
    return False


# ── API Key ─────────────────────────────────────────────

def get_api_key():
    """从环境变量或本地 .env 获取 DeepSeek API key"""
    # Try environment first
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    
    # Parse from local/shared .env file
    env_path = env_file()
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('DEEPSEEK_API_KEY='):
                    key = line.split('=', 1)[1].strip().strip("'\"")
                    if key:
                        return key
    
    raise RuntimeError(f"No DeepSeek API key found in environment or {env_path}")


# ── Novel Sampling ──────────────────────────────────────

def find_novel_file(title):
    """根据标题找到小说文件"""
    for f in REF_DIR.iterdir():
        if not f.is_file():
            continue
        name = f.stem
        # Match: 《书名》[作者].txt or 书名.txt
        if title in name:
            return f
    return None


def sample_novel(filepath, sample_chars=SAMPLE_SIZE):
    """从小说中采样：开头+中段+结尾+章节列表"""
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        text = f.read()
    
    total = len(text)
    
    # 1. 开头
    head = text[:sample_chars]
    
    # 2. 中段（40%位置）
    mid_start = int(total * 0.4)
    mid = text[mid_start:mid_start + sample_chars]
    
    # 3. 结尾
    tail = text[max(0, total - sample_chars):]
    
    # 4. 章节标题列表（最多50个）
    chapter_patterns = [
        re.compile(r'^[ \t]*第[零一二三四五六七八九十百千万\d]+[章回节卷].{0,30}', re.M),
        re.compile(r'^[ \t]*\d{1,4}[、.]\s*.{2,30}', re.M),
    ]
    chapters = []
    for pat in chapter_patterns:
        chapters.extend(pat.findall(text))
    chapters = chapters[:50]
    chapter_list = "\n".join(f"  {c.strip()}" for c in chapters)
    
    return {
        "total_chars": total,
        "head": head,
        "mid": mid,
        "tail": tail,
        "chapter_list": chapter_list,
        "chapter_count": len(chapters),
    }


# ── LLM Anchor Generation ──────────────────────────────

ANCHOR_PROMPT = """你是一位文学分析专家。请为小说《{title}》生成{target}个精华锚点。

每个锚点代表小说中一个值得学习的精彩片段/场景/转折/人物刻画/哲学思考。

要求：
1. 锚点必须覆盖小说的不同部分（前/中/后期均匀分布）
2. 类别多样化：场景/高潮、人物/成长、心理/内心、转折/伏笔、哲学/世界观、关系/冲突 各至少3个
3. 每个锚点必须包含4个维度的预期文本样本（每维度3段，每段50-150字）
4. 预期文本必须像真实小说原文，包含具体的人名、地名、动作、对话
5. scenes维度：环境描写、动作场面、氛围渲染
6. psychology维度：内心独白、情绪波动、心理转变
7. characters维度：人物外貌、性格展现、关系互动
8. twists维度：伏笔埋设、反转揭示、悬念制造

以下是小说的采样片段供参考：

【开头片段】
{head}

【中段片段】
{mid}

【结尾片段】
{tail}

【章节目录（共{chapter_count}章）】
{chapter_list}

请输出JSON数组格式，每个元素结构如下：
```json
[
  {{
    "label": "锚点名称（4-8字）",
    "category": "类别（如：场景/高潮、人物/成长、心理/内心、转折/伏笔）",
    "quote": "一句话概括这个锚点的核心内容",
    "dimensions": {{
      "scenes": ["场景描写样本1(50-150字)", "样本2", "样本3"],
      "psychology": ["心理描写样本1(50-150字)", "样本2", "样本3"],
      "characters": ["人物描写样本1(50-150字)", "样本2", "样本3"],
      "twists": ["伏笔/反转样本1(50-150字)", "样本2", "样本3"]
    }}
  }}
]
```

直接输出JSON数组，不要其他文字。确保生成{target}个锚点。"""


def call_llm(prompt, api_key, model="deepseek-chat", max_tokens=16000):
    """调用DeepSeek API"""
    import urllib.request
    import urllib.error
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(API_URL, data=data, headers=headers, method='POST')
    
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            content = result['choices'][0]['message']['content']
            return content
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8') if e.fp else ''
        raise RuntimeError(f"API error {e.code}: {error_body[:200]}")


def fix_json_string(text):
    """修复LLM输出的常见JSON问题"""
    # Remove trailing commas before ] or }
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # Fix unescaped newlines in strings (replace with \\n)
    # This is tricky - only fix within quoted strings
    # Simple approach: try to fix common truncation
    # If JSON is truncated mid-array, close it
    open_brackets = text.count('[') - text.count(']')
    open_braces = text.count('{') - text.count('}')
    if open_brackets > 0 or open_braces > 0:
        # Truncated - try to close gracefully
        # Remove the last incomplete object
        last_complete = text.rfind('},')
        if last_complete > 0:
            text = text[:last_complete + 1]
        # Close remaining brackets
        text += ']' * max(0, open_brackets)
    return text


def parse_anchors_response(response_text):
    """解析LLM返回的JSON，带多级修复"""
    text = response_text.strip()
    
    # Remove markdown code blocks
    if '```' in text:
        text = re.sub(r'^```(?:json)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
        text = text.strip()
    
    # Strategy 1: direct parse
    try:
        anchors = json.loads(text)
    except json.JSONDecodeError:
        # Strategy 2: extract JSON array
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                anchors = json.loads(match.group())
            except json.JSONDecodeError:
                # Strategy 3: fix common issues then parse
                fixed = fix_json_string(match.group())
                try:
                    anchors = json.loads(fixed)
                except json.JSONDecodeError:
                    # Strategy 4: parse with json5-like tolerance
                    # Try removing control characters
                    cleaned = re.sub(r'[\x00-\x1f]', ' ', fixed)
                    cleaned = cleaned.replace('\n', '\\n')
                    try:
                        anchors = json.loads(cleaned)
                    except json.JSONDecodeError as e:
                        raise ValueError(f"All JSON parse strategies failed: {e}")
        else:
            raise ValueError("No JSON array found in response")
    
    if not isinstance(anchors, list):
        raise ValueError(f"Expected list, got {type(anchors)}")
    
    # Validate structure
    valid = []
    for a in anchors:
        if not isinstance(a, dict):
            continue
        if 'label' not in a or 'dimensions' not in a:
            continue
        dims = a['dimensions']
        if not isinstance(dims, dict):
            continue
        # Ensure all 4 dimensions exist
        for dim in ['scenes', 'psychology', 'characters', 'twists']:
            if dim not in dims or not isinstance(dims[dim], list):
                dims[dim] = []
        # Add category if missing
        if 'category' not in a:
            a['category'] = '未分类'
        if 'quote' not in a:
            a['quote'] = a['label']
        valid.append(a)
    
    return valid


# ── Main Logic ──────────────────────────────────────────

def get_novels_needing_expansion(target):
    """找出需要扩张锚点的小说"""
    needs = []
    for novel_dir in sorted(EXTRACTED_DIR.iterdir()):
        if not novel_dir.is_dir():
            continue
        title = novel_dir.name
        anchor_file = ANCHOR_DIR / f"{title}.json"
        
        current_count = 0
        if anchor_file.exists():
            with open(anchor_file, 'r') as f:
                data = json.load(f)
            current_count = len(data) if isinstance(data, list) else 0
        
        if current_count < target:
            # Find the novel file
            novel_file = find_novel_file(title)
            if novel_file:
                needs.append({
                    "title": title,
                    "file": novel_file,
                    "current_anchors": current_count,
                    "needed": target - current_count,
                })
    
    return needs


def expand_novel(title, novel_file, target, api_key, existing_anchors=None):
    """为一本小说生成锚点"""
    print(f"  📖 采样 《{title}》...")
    sample = sample_novel(novel_file)
    
    prompt = ANCHOR_PROMPT.format(
        title=title,
        target=target,
        head=sample['head'][:4000],
        mid=sample['mid'][:4000],
        tail=sample['tail'][:4000],
        chapter_count=sample['chapter_count'],
        chapter_list=sample['chapter_list'][:2000],
    )
    
    print(f"  🤖 调用LLM生成{target}个锚点...")
    for attempt in range(MAX_RETRIES):
        try:
            response = call_llm(prompt, api_key)
            anchors = parse_anchors_response(response)
            print(f"  ✅ 成功解析 {len(anchors)} 个锚点")
            return anchors
        except Exception as e:
            print(f"  ⚠️ 尝试{attempt+1}失败: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(3)
    
    return None


def main():
    parser = argparse.ArgumentParser(description="锚点扩张器")
    parser.add_argument('--novel', type=str, help='只处理指定小说')
    parser.add_argument('--dry-run', action='store_true', help='只显示计划')
    parser.add_argument('--workers', type=int, default=2, help='并发数')
    parser.add_argument('--target', type=int, default=TARGET_ANCHORS, help='目标锚点数')
    parser.add_argument('--model', type=str, default='deepseek-chat', help='LLM模型')
    args = parser.parse_args()
    
    print(f"🚀 锚点扩张器 v1.0")
    print(f"   目标: 每本{args.target}个锚点")
    print(f"   模型: {args.model}")
    print(f"   并发: {args.workers}")
    print()
    
    # Get API key
    try:
        api_key = get_api_key()
        print(f"   API Key: ...{api_key[-6:]}")
    except Exception as e:
        print(f"❌ 无法获取API Key: {e}")
        sys.exit(1)
    
    # Find novels needing expansion
    if args.novel:
        novel_file = find_novel_file(args.novel)
        if not novel_file:
            print(f"❌ 找不到小说文件: {args.novel}")
            sys.exit(1)
        
        anchor_file = ANCHOR_DIR / f"{args.novel}.json"
        current = 0
        if anchor_file.exists():
            with open(anchor_file, 'r') as f:
                current = len(json.load(f))
        
        needs = [{
            "title": args.novel,
            "file": novel_file,
            "current_anchors": current,
            "needed": args.target,
        }]
    else:
        needs = get_novels_needing_expansion(args.target)
    
    print(f"📋 需要扩张: {len(needs)} 本小说")
    for n in needs[:10]:
        print(f"   {n['title']:20s} 当前:{n['current_anchors']:2d} → 目标:{args.target}")
    if len(needs) > 10:
        print(f"   ... 还有 {len(needs)-10} 本")
    print()
    
    if args.dry_run:
        print("🏁 Dry run 完成，不执行实际操作")
        return
    
    # Process novels
    success = 0
    failed = 0
    
    for i, novel_info in enumerate(needs):
        title = novel_info['title']
        
        # Skip if already done (from previous run)
        if already_done(title, args.target):
            print(f"\n[{i+1}/{len(needs)}] 《{title}》 ⏭️ 已完成，跳过")
            success += 1
            continue
        
        print(f"\n{'='*60}")
        print(f"[{i+1}/{len(needs)}] 《{title}》")
        print(f"{'='*60}")
        
        anchors = expand_novel(
            title=title,
            novel_file=novel_info['file'],
            target=args.target,
            api_key=api_key,
        )
        
        if anchors and len(anchors) >= 10:
            # Save anchors
            anchor_file = ANCHOR_DIR / f"{title}.json"
            with open(anchor_file, 'w', encoding='utf-8') as f:
                json.dump(anchors, f, ensure_ascii=False, indent=2)
            print(f"  💾 保存 {len(anchors)} 个锚点 → {anchor_file.name}")
            success += 1
        else:
            print(f"  ❌ 失败（生成不足10个锚点）")
            failed += 1
        
        # Rate limiting
        if i < len(needs) - 1:
            time.sleep(1)
    
    print(f"\n{'='*60}")
    print(f"🏁 完成: 成功 {success}, 失败 {failed}")
    print(f"   下一步: python3 anchor-matcher.py 重新匹配原文")


if __name__ == "__main__":
    main()
