"""Document loader for 001 project — auto-extract structured content from MD files.

Provides chapter outline extraction, character profile extraction, and source
constraint loading. Migrated from scripts/material_assembler.py into the
LangChain engine for direct chain integration.

Usage:
    from langchain_engine.doc_loader import (
        extract_chapter_outline,
        extract_character_profiles,
        extract_source_constraints,
        load_project_settings,
    )
"""

import re
import sys
from pathlib import Path
from typing import List, Optional

# ============================================================
# PROJECT PATHS
# ============================================================

for parent in Path(__file__).resolve().parents:
    if (parent / "writing_paths.py").exists():
        sys.path.insert(0, str(parent))
        break
from writing_paths import DEFAULT_NOVEL_DIR

PROJECT_DIR = DEFAULT_NOVEL_DIR
OUTLINE_FILE = PROJECT_DIR / "高中卷大纲.md"
CHARACTERS_FILE = PROJECT_DIR / "人物档案.md"
SETTINGS_FILE = PROJECT_DIR / "001设定及问题.md"


# ============================================================
# CHAPTER OUTLINE EXTRACTION
# ============================================================

def extract_chapter_outline(chapter_num: int,
                            outline_path: Optional[Path] = None) -> str:
    """从大纲文件中精确提取指定章节的块。

    支持格式：### 第N章 / ### Ch N / ## 第N章 / 中文数字

    Args:
        chapter_num: 章节号 (1-based)
        outline_path: 大纲文件路径，默认使用高中卷大纲.md

    Returns:
        章节大纲文本（含标题），找不到返回空字符串
    """
    if outline_path is None:
        if OUTLINE_FILE.exists():
            outline_path = OUTLINE_FILE
        else:
            # Fallback: 搜索其他大纲文件
            candidates = list(PROJECT_DIR.glob("*大纲*"))
            candidates += list(PROJECT_DIR.glob("*outline*"))
            candidates = [c for c in candidates if c.is_file() and c.suffix == ".md"
                          and "重构方案" not in c.name]
            if not candidates:
                return ""
            outline_path = candidates[0]

    content = outline_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # 中文数字映射
    cn_nums = "零一二三四五六七八九十"
    if chapter_num <= 10:
        cn_num = cn_nums[chapter_num]
    elif chapter_num < 20:
        cn_num = f"十{cn_nums[chapter_num - 10]}" if chapter_num > 10 else "十"
    else:
        cn_num = str(chapter_num)

    # 多种标题格式
    start_patterns = [
        rf"^#{{1,3}}\s*第{chapter_num}章",
        rf"^#{{1,3}}\s*第{cn_num}章",
        rf"^#{{1,3}}\s*Ch\s*{chapter_num}\b",
        rf"^#{{1,3}}\s*Chapter\s*{chapter_num}\b",
        rf"^#{{1,3}}\s*第{chapter_num}节",
    ]

    start_idx = -1
    start_level = 0
    for i, line in enumerate(lines):
        for pat in start_patterns:
            if re.match(pat, line, re.IGNORECASE):
                start_idx = i
                start_level = len(line) - len(line.lstrip("#"))
                break
        if start_idx >= 0:
            break

    if start_idx < 0:
        return ""

    # 找到下一个同级或更高级标题作为结束
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        if re.match(r"^#{1,3}\s", line):
            level = len(line) - len(line.lstrip("#"))
            if level <= start_level:
                end_idx = i
                break

    return "\n".join(lines[start_idx:end_idx]).strip()


# ============================================================
# CHARACTER PROFILE EXTRACTION
# ============================================================

def extract_character_profiles(names: List[str],
                               characters_path: Optional[Path] = None,
                               max_chars_per_character: int = 1200) -> str:
    """从人物档案中提取指定角色的完整段落。

    策略：
    1. 查找角色标题段落（### 角色名 或含角色名的标题）
    2. Fallback: grep包含角色名的上下文

    Args:
        names: 角色名列表
        characters_path: 人物档案路径
        max_chars_per_character: 每个角色最大字符数

    Returns:
        拼接后的人物档案文本，角色之间用 --- 分隔
    """
    if characters_path is None:
        characters_path = CHARACTERS_FILE
    if not characters_path.exists():
        return ""

    content = characters_path.read_text(encoding="utf-8")
    profiles = []

    for name in names:
        if not name or not name.strip():
            continue

        # 策略1：查找角色标题段落
        # 支持格式: ### 封云 / ## 封云（主角） / ### 2. 封云
        pattern = (
            rf"(#{1,3}\s*(?:\d+\.\s*)?[^#\n]*"
            rf"{re.escape(name.strip())}[^\n]*\n[\s\S]*?)"
            rf"(?=\n#{1,3}\s|\Z)"
        )
        m = re.search(pattern, content)
        if m:
            profiles.append(m.group(1)[:max_chars_per_character])
        else:
            # 策略2：grep包含角色名的段落（前后文）
            lines = content.split("\n")
            found = False
            for i, line in enumerate(lines):
                if name.strip() in line:
                    start = max(0, i - 2)
                    end = min(len(lines), i + 12)
                    snippet = "\n".join(lines[start:end])
                    profiles.append(
                        f"[含{name}的段落]\n{snippet[:max_chars_per_character]}"
                    )
                    found = True
                    break
            if not found:
                profiles.append(f"[人物档案中未找到: {name}]")

    return "\n---\n".join(profiles) if profiles else ""


def list_all_characters(characters_path: Optional[Path] = None) -> List[str]:
    """列出人物档案中所有角色名。"""
    if characters_path is None:
        characters_path = CHARACTERS_FILE
    if not characters_path.exists():
        return []

    content = characters_path.read_text(encoding="utf-8")
    # 提取标题中的角色名: ### 封云 / ### 2. 李雪梅（班主任）
    names = []
    for m in re.finditer(r"^#{2,3}\s*(?:\d+\.\s*)?([^\s（(·#]+)", content, re.MULTILINE):
        name = m.group(1).strip()
        if len(name) >= 2 and not name.startswith("第") and not name.startswith("Ch"):
            names.append(name)
    return names


# ============================================================
# SOURCE CONSTRAINTS EXTRACTION
# ============================================================

def extract_source_constraints(max_chars: int = 3000) -> str:
    """提取项目核心约束（立意/世界观/时间线/设定）。

    扫描项目目录中的 conception/立意/设定/穿越 等文档。
    """
    constraints = []
    search_patterns = ["*conception*", "*立意*", "*穿越设定*", "*双线叙事*"]

    for pattern in search_patterns:
        for f in PROJECT_DIR.glob(pattern):
            if f.is_file() and f.suffix == ".md":
                text = f.read_text(encoding="utf-8")[:1500]
                constraints.append(f"[源文档·{f.name}]\n{text}")

    return "\n\n".join(constraints)[:max_chars]


# ============================================================
# PROJECT SETTINGS (001设定及问题.md)
# ============================================================

def load_project_settings(max_chars: int = 4000) -> str:
    """加载001设定及问题.md — 终极命题/世界观/穿越顺序。

    提取核心设定段落，去除对话式内容。
    """
    if not SETTINGS_FILE.exists():
        return ""

    content = SETTINGS_FILE.read_text(encoding="utf-8")
    lines = content.split("\n")

    # 提取关键段落（跳过对话式开头行）
    key_sections = []
    current_section = []
    in_key_section = False

    for line in lines:
        stripped = line.strip()
        # 识别关键设定内容（非对话/请求行）
        if any(kw in stripped for kw in [
            "世界", "穿越", "三国", "玄幻", "神话", "奇幻", "修仙",
            "朋克", "主题", "命题", "成长", "回归", "现实",
            "16岁", "19岁", "24岁", "30岁", "36岁", "60岁",
            "突破", "否定", "亲情", "友情", "爱情"
        ]):
            in_key_section = True
        elif stripped.startswith("加载技能") or stripped.startswith("请帮我"):
            in_key_section = False
            if current_section:
                key_sections.append("\n".join(current_section))
                current_section = []
            continue

        if in_key_section and stripped:
            current_section.append(stripped)

    if current_section:
        key_sections.append("\n".join(current_section))

    result = "\n\n".join(key_sections)
    return result[:max_chars] if result else content[:max_chars]


# ============================================================
# CONVENIENCE: Load all context for a chapter
# ============================================================

def load_chapter_context(chapter_num: int,
                         character_names: Optional[List[str]] = None) -> dict:
    """一键加载指定章节所需的全部文档上下文。

    Returns:
        {
            "outline_section": str,  # 本章大纲块
            "characters": str,       # 相关人物档案
            "constraints": str,      # 源约束
            "settings": str,         # 终极命题/世界观
        }
    """
    outline = extract_chapter_outline(chapter_num)

    # 如果没有指定角色名，从大纲中自动提取
    if character_names is None:
        character_names = _extract_names_from_outline(outline)

    characters = extract_character_profiles(character_names) if character_names else ""
    constraints = extract_source_constraints()
    settings = load_project_settings(max_chars=2000)

    return {
        "outline_section": outline,
        "characters": characters,
        "constraints": constraints,
        "settings": settings,
    }


def _extract_names_from_outline(outline: str) -> List[str]:
    """从大纲文本中提取可能的角色名。

    策略：对比人物档案中的已注册角色名。
    """
    all_chars = list_all_characters()
    if not all_chars:
        return []
    found = []
    for name in all_chars:
        if name in outline:
            found.append(name)
    return found
