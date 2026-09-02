from __future__ import annotations

import re


_VISIBLE_SOURCE_INSTRUCTION_PATTERNS = [
    (
        r"每段写完后[^\n。；;]*标注材料来源[^\n。；;]*[。；;]?",
        "每段完成后只做内部材料依据核对，不在正文输出来源标签。",
    ),
    (
        r"每段结尾用方括号标注材料来源[^\n。；;]*[。；;]?",
        "每段结尾不要输出材料来源、五维、技法或源文档标签。",
    ),
    (
        r"每个事件标注来源[^\n。；;]*[。；;]?",
        "每个事件需内部可追溯材料依据，不在成稿中输出来源标签。",
    ),
    (
        r"如需标注来源[^\n。；;]*[。；;]?",
        "材料来源仅用于内部核对，不在成稿中输出来源标签。",
    ),
    (
        r"段尾标注来源[^\n。；;]*[。；;]?",
        "段尾不要输出来源标签。",
    ),
]


def sanitize_delivery_boundary_text(text: str | None) -> str:
    """Neutralize legacy specs that ask the model to print source labels.

    Source traceability remains an internal quality gate. User-adoptable drafts
    must not carry source-document, five-dimension, or technique tail notes.
    """
    value = str(text or "")
    if not value:
        return ""
    original = value
    for pattern, replacement in _VISIBLE_SOURCE_INSTRUCTION_PATTERNS:
        value = re.sub(pattern, replacement, value)
    if re.search(r"标注材料来源|方括号标注|段尾标注来源", original):
        guard = "交付边界：材料来源只用于内部追溯与审查，不得写入用户可采纳正文或结构稿。"
        if guard not in value:
            value = value.rstrip() + "\n" + guard
    return value
