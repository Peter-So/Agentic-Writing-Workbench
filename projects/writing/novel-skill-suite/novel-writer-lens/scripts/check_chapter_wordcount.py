#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


MD_RE = re.compile(r"(?m)^(#{1,6}\s*|>\s*|\s*[-*+]\s*|\s*\d+\.\s*)")
LINK_RE = re.compile(r"\[(.*?)\]\(.*?\)")
INLINE_RE = re.compile(r"(\*\*|\*|~~|`)")


def count_chinese_chars(text: str) -> int:
    text = LINK_RE.sub(r"\1", text)
    text = INLINE_RE.sub("", text)
    text = MD_RE.sub("", text)
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def extract_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#") and "章" in line:
            return "\n".join(lines[i + 1 :])
    return text


def check_file(path: Path, min_words: int = 3600) -> dict[str, object]:
    if not path.exists():
        return {"file": str(path), "exists": False, "word_count": 0, "status": "error"}
    body = extract_body(path)
    count = count_chinese_chars(body)
    return {
        "file": str(path),
        "exists": True,
        "word_count": count,
        "status": "pass" if count >= min_words else "fail",
        "min_words": min_words,
    }


def print_result(result: dict[str, object]) -> None:
    if not result["exists"]:
        print(f"missing: {result['file']}")
        return
    status = result["status"]
    count = result["word_count"]
    min_words = result["min_words"]
    print(f"{status}: {count} chars (min {min_words})")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python check_chapter_wordcount.py <file> [min_words]")
        print("   or: python check_chapter_wordcount.py --all <dir> [min_words]")
        return 1

    if sys.argv[1] == "--all":
        if len(sys.argv) < 3:
            print("missing directory")
            return 1
        directory = Path(sys.argv[2])
        min_words = int(sys.argv[3]) if len(sys.argv) > 3 else 3600
        files = sorted(directory.glob("*.md"))
        for file in files:
            print_result(check_file(file, min_words))
        return 0

    path = Path(sys.argv[1])
    min_words = int(sys.argv[2]) if len(sys.argv) > 2 else 3600
    print_result(check_file(path, min_words))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
