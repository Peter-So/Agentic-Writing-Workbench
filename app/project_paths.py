# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from app.novel_context import normalize_novel_id, novel_dir


DIR_NAMES = {
    "settings": "设定",
    "planning": "规划",
    "characters": "人物",
    "memory": "记忆",
    "style": "风格",
    "chapters": "正文",
    "outputs": "输出",
    "accepted_planning": "已采纳规划",
    "outline_backups": "大纲备份",
    "logs": "日志",
    "invocations": "调用记录",
    "skills": "技能",
    "wiki": "维基",
    "assets": "资产",
    "references": "参考材料",
    "brief": "简报",
    "development": "开发",
    "script": "剧本",
    "storyboard": "分镜",
    "storyboards": "分镜生成",
    "notes": "随想",
    "ideas": "灵感",
    "drafts": "草稿",
}

LEGACY_DIR_NAMES = {
    "settings": "settings",
    "planning": "planning",
    "characters": "characters",
    "memory": "memory",
    "style": "style",
    "chapters": "chapters",
    "outputs": "outputs",
    "logs": "logs",
    "invocations": "invocations",
    "skills": "skills",
    "wiki": "wiki",
    "assets": "assets",
    "references": "references",
    "brief": "brief",
    "development": "development",
    "script": "script",
    "storyboard": "storyboard",
    "storyboards": "storyboards",
    "notes": "notes",
    "ideas": "ideas",
    "drafts": "drafts",
}


def cn_dir(key: str) -> str:
    return DIR_NAMES[key]


def legacy_dir(key: str) -> str:
    return LEGACY_DIR_NAMES[key]


def project_path(novel_id: str | None, *parts: str | Path) -> Path:
    return novel_dir(normalize_novel_id(novel_id)).joinpath(*map(str, parts))


def project_dir(novel_id: str | None, key: str, *parts: str | Path, prefer_existing: bool = True) -> Path:
    base = novel_dir(normalize_novel_id(novel_id))
    primary = base / cn_dir(key)
    legacy = base / legacy_dir(key)
    root = legacy if prefer_existing and legacy.exists() and not primary.exists() else primary
    return root.joinpath(*map(str, parts))


def wiki_dir(novel_id: str | None) -> Path:
    return project_dir(novel_id, "wiki")


def outputs_dir(novel_id: str | None, *parts: str | Path) -> Path:
    return project_dir(novel_id, "outputs", *parts)


def logs_invocations_dir(novel_id: str | None) -> Path:
    return project_dir(novel_id, "logs", cn_dir("invocations"))


def skills_dir(novel_id: str | None) -> Path:
    return project_dir(novel_id, "skills")


def assets_dir(novel_id: str | None, *parts: str | Path) -> Path:
    return project_dir(novel_id, "assets", *parts)


def storyboards_dir(novel_id: str | None, *parts: str | Path) -> Path:
    return project_dir(novel_id, "storyboards", *parts)
