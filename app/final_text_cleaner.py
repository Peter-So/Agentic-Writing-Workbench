from __future__ import annotations

import re


_INLINE_TAG_RE = re.compile(
    r"[\[【]\s*(?:"
    r"五维|技法|源文档|原文档|provider|Provider|角色|人物|材料|本章大纲|前情|人物设定|参考技法"
    r")[^\]】]*[\]】]"
)
_TRAILING_SOURCE_NOTE_RE = re.compile(
    r"\s*[（(]\s*(?:"
    r"五维|技法|源文档|原文档|provider|Provider|角色|人物|材料|来源"
    r")[^）)]*[）)]\s*$"
)
_SOURCE_SECTION_RE = re.compile(
    r"^\s{0,3}#{1,4}\s*(?:技法来源|材料来源|来源说明|引用说明|参考来源|溯源|Source Notes?)\s*$",
    re.IGNORECASE,
)
_SOURCE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:"
    r"来源|材料来源|引用来源|provider|Provider|五维|技法|源文档|原文档"
    r")\s*[：:｜|].*$"
)


def clean_final_draft(text: str, *, task: str = "", project_kind: str = "") -> str:
    """Return user-facing final content without internal provenance labels.

    Provider answers and merge metadata keep provenance in artifacts; the draft
    that users confirm/archive must be readable content only.
    """
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        return ""

    lines = value.split("\n")
    kept: list[str] = []
    skipping_source_section = False
    for raw in lines:
        line = raw.rstrip()
        if _SOURCE_SECTION_RE.match(line):
            skipping_source_section = True
            continue
        if skipping_source_section:
            if not line.strip():
                skipping_source_section = False
                continue
            if re.match(r"^\s{0,3}#{1,4}\s+\S+", line) or line.strip() == "---":
                skipping_source_section = False
            else:
                continue
        if _SOURCE_LINE_RE.match(line) and len(line.strip()) <= 180:
            continue
        line = _INLINE_TAG_RE.sub("", line)
        line = _TRAILING_SOURCE_NOTE_RE.sub("", line)
        line = re.sub(r"[ \t]{2,}", " ", line)
        kept.append(line.rstrip())

    cleaned = "\n".join(kept)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned.strip()
