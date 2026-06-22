#!/usr/bin/env python3
"""
网文下载器 — 多源容错，逐章下载中文网络小说。
源优先级：书趣阁 > 全本小说网 > txt80 > 笔趣阁镜像
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import NOVEL_ACQUISITION_DIR

# ── Config ──────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
REQUEST_TIMEOUT = 20
RETRY_COUNT = 2
CHAPTER_DELAY = 1.5  # 章节间延迟，防封
OUTPUT_DIR = NOVEL_ACQUISITION_DIR / "novels"
STATE_FILE = NOVEL_ACQUISITION_DIR / "state.json"

# 代理配置（Windows 本地优先读取当前进程环境变量）
def _get_proxy():
    """读取代理配置。"""
    return os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or ""

PROXY_URL = _get_proxy()
_proxy_handler = None
if PROXY_URL:
    _proxy_handler = urllib.request.ProxyHandler({
        "https": PROXY_URL,
        "http": PROXY_URL.replace("https://", "http://"),
    })
    _opener = urllib.request.build_opener(_proxy_handler)
    urllib.request.install_opener(_opener)


def fetch(url, timeout=REQUEST_TIMEOUT, retries=RETRY_COUNT):
    """Fetch URL with retry and encoding detection."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                for enc in ['utf-8', 'gbk', 'gb2312', 'gb18030']:
                    try:
                        return data.decode(enc)
                    except UnicodeDecodeError:
                        continue
                return data.decode('utf-8', errors='replace')
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
            else:
                print(f"  ⚠ fetch failed: {url[:80]} — {e}", file=sys.stderr)
                return None


def search_novel_online(title, author):
    """Search for novel across multiple sources. Returns (source_name, book_url)."""
    encoded = urllib.parse.quote(title)

    sources = [
        # txt80 (通过代理最稳定)
        ("txt80", f"https://www.txt80.com/search/?key={encoded}"),
        # 书趣阁
        ("shuquge", f"https://www.shuquge.com/search.html?keyword={encoded}"),
        # 全本小说网
        ("qb5", f"https://www.qb5.tw/search.php?keyword={encoded}"),
    ]

    for name, url in sources:
        html = fetch(url)
        if not html:
            continue

        # 尝试提取第一个小说链接
        links = re.findall(r'href="([^"]+)"[^>]*>([^<]{3,80})</a>', html)
        for link_url, link_text in links:
            link_text = link_text.strip()
            # 检查是否匹配书名
            if title[:2] in link_text or title[:3] in link_text:
                # 确保是绝对URL
                if not link_url.startswith("http"):
                    base = "/".join(url.split("/")[:3])
                    link_url = base + link_url if link_url.startswith("/") else base + "/" + link_url
                return name, link_url

    # 如果搜索没结果，尝试直接构建URL（常见模式）
    return None, None


def extract_chapters(book_url, source_name):
    """Extract chapter list from book page. Returns [(chapter_title, chapter_url), ...]"""
    html = fetch(book_url)
    if not html:
        return []

    chapters = []
    # 通用章节链接提取
    pattern = r'<a[^>]*href="([^"]+\.html?)"[^>]*>([^<]{2,80})</a>'
    matches = re.findall(pattern, html, re.IGNORECASE)

    for href, title in matches:
        title = re.sub(r'<[^>]+>', '', title).strip()
        # 过滤非章节链接
        if not title or len(title) < 2 or len(title) > 60:
            continue
        if re.search(r'首页|目录|下一页|上一页|排行|搜索|登录|注册|下载|简介', title):
            continue

        # 构造完整URL
        if not href.startswith("http"):
            if href.startswith("/"):
                base = "/".join(book_url.split("/")[:3])
                href = base + href
            else:
                href = book_url.rsplit("/", 1)[0] + "/" + href

        chapters.append((title, href))

    # 去重
    seen = set()
    unique = []
    for t, u in chapters:
        if u not in seen:
            seen.add(u)
            unique.append((t, u))

    return unique


def extract_content(chapter_url):
    """Extract chapter text content."""
    html = fetch(chapter_url)
    if not html:
        return ""

    # 去除script/style标签
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # 常见正文容器
    content_patterns = [
        r'<div[^>]*id="content"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*id="chaptercontent"[^>]*>(.*?)</div>',
        r'<article[^>]*>(.*?)</article>',
        r'<div[^>]*class="[^"]*txt[^"]*"[^>]*>(.*?)</div>',
    ]

    content = ""
    for pattern in content_patterns:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            content = match.group(1)
            break

    if not content:
        # 回退：取所有<p>标签
        paragraphs = re.findall(r'<p[^>]*>(.{10,500})</p>', html, re.DOTALL)
        content = "\n".join(paragraphs)

    # 清理
    content = re.sub(r'<br\s*/?>', '\n', content)
    content = re.sub(r'<[^>]+>', '', content)
    content = re.sub(r'&nbsp;', ' ', content)
    content = re.sub(r'&lt;', '<', content)
    content = re.sub(r'&gt;', '>', content)
    content = re.sub(r'&amp;', '&', content)
    content = re.sub(r'&quot;', '"', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    content = content.strip()

    # 去除常见广告和提示
    content = re.sub(r'请记住本书首发域名.*?\n', '', content)
    content = re.sub(r'一秒记住.*?\n', '', content)
    content = re.sub(r'天才一秒记住.*?\n', '', content)
    content = re.sub(r'手机用户请浏览.*?\n', '', content)

    return content


def download_novel(title, author, output_root):
    """Download a complete novel. Returns (success, chapter_count, error_msg)."""
    novel_dir = output_root / title
    novel_dir.mkdir(parents=True, exist_ok=True)

    # 1. 搜索小说
    print(f"🔍 搜索: 《{title}》 {author}", file=sys.stderr)
    source, book_url = search_novel_online(title, author)

    if not book_url:
        # 尝试已知URL模式
        print(f"  ⚠ 搜索无结果，尝试已知书源...", file=sys.stderr)
        for src, url_template in KNOWN_URLS.get(title, []):
            html = fetch(url_template)
            if html and len(html) > 2000:
                source, book_url = src, url_template
                print(f"  ✅ 命中已知书源: {src}", file=sys.stderr)
                break

    if not book_url:
        return False, 0, f"未找到可用的书源"

    print(f"  📚 书源: {source} → {book_url[:60]}...", file=sys.stderr)

    # 2. 提取章节列表
    chapters = extract_chapters(book_url, source)
    if not chapters:
        return False, 0, f"未能提取章节列表"

    print(f"  📑 章节数: {len(chapters)}", file=sys.stderr)

    # 3. 下载章节
    success_count = 0
    all_content = []

    for i, (ch_title, ch_url) in enumerate(chapters):
        if i > 0:
            time.sleep(CHAPTER_DELAY)

        content = extract_content(ch_url)
        if content and len(content) > 100:
            # 保存单章
            safe_title = re.sub(r'[\\/*?:"<>|]', '', ch_title)[:50]
            ch_file = novel_dir / f"{i:05d}_{safe_title}.txt"
            with open(ch_file, 'w', encoding='utf-8') as f:
                f.write(f"{ch_title}\n\n{content}")

            all_content.append(f"\n\n{'='*40}\n{ch_title}\n{'='*40}\n\n{content}")
            success_count += 1

        if (i + 1) % 50 == 0:
            print(f"    ... {i+1}/{len(chapters)} 章已完成", file=sys.stderr)

    # 4. 保存全文
    if all_content:
        full_text = "\n".join(all_content)
        full_file = novel_dir / "novel.txt"
        with open(full_file, 'w', encoding='utf-8') as f:
            f.write(full_text)

    print(f"  ✅ 下载完成: {success_count}/{len(chapters)} 章", file=sys.stderr)
    return True, success_count, None


# ── 已知书源URL（搜索结果不稳定时的回退） ────────────────
KNOWN_URLS = {}  # 运行时动态填充


def main():
    if len(sys.argv) < 2:
        print("Usage: downloader.py <novel_list.json> [--single '书名']", file=sys.stderr)
        sys.exit(1)

    novel_file = sys.argv[1]
    single_novel = None
    if len(sys.argv) >= 4 and sys.argv[2] == '--single':
        single_novel = sys.argv[3]

    with open(novel_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    novels = data.get("novels", [])
    if single_novel:
        novels = [n for n in novels if n["title"] == single_novel]

    results = []
    for novel in novels:
        title = novel["title"]
        author = novel["author"]
        print(f"\n{'='*50}", file=sys.stderr)
        print(f"📖 《{title}》 — {author}", file=sys.stderr)

        success, count, error = download_novel(title, author, OUTPUT_DIR)
        result = {
            "title": title,
            "author": author,
            "success": success,
            "chapters": count,
            "error": error,
        }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))

    # 汇总
    ok = sum(1 for r in results if r["success"])
    fail = sum(1 for r in results if not r["success"])
    total_chapters = sum(r["chapters"] for r in results if r["success"])
    print(f"\n📊 汇总: {ok} 成功, {fail} 失败, 共 {total_chapters} 章", file=sys.stderr)


if __name__ == "__main__":
    main()
