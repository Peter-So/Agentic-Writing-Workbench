from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import novel_dir, normalize_novel_id
from app.project_paths import wiki_dir


STRUCTURE_VERSION = 1

NOVEL_STRUCTURAL_DOCS = {
    "base_setting": {
        "label": "基础设定",
        "path": "设定/基础设定.md",
        "aliases": ["settings/基础设定.md", "基础设定.md", "brief.md", "logline.md", "项目简报.md", "设定.md"],
        "description": "题材、类型基调、核心命题、创作约束等全局基础信息。",
        "template": (
            "# 基础设定\n\n"
            "## 项目一句话\n待补充。\n\n"
            "## 类型与基调\n待补充。\n\n"
            "## 核心命题\n待补充。\n\n"
            "## 创作约束\n待补充。\n"
        ),
    },
    "character": {
        "label": "人物设定",
        "path": "人物/人物档案.md",
        "aliases": ["characters/人物档案.md", "人物档案.md", "characters.md", "角色表.md", "人物设定.md"],
        "description": "主角、配角、欲望、阻碍、关系、弧光、声音等人物材料。",
        "template": (
            "# 人物档案\n\n"
            "## 主角\n"
            "- 姓名：待定\n"
            "- 欲望：待定\n"
            "- 阻碍：待定\n"
            "- 转变：待定\n\n"
            "## 重要配角\n待补充。\n"
        ),
    },
    "worldview": {
        "label": "世界观设定",
        "path": "设定/世界观设定.md",
        "aliases": ["settings/世界观设定.md", "世界观设定.md", "world.md", "001设定及问题.md", "穿越设定.md"],
        "description": "世界规则、时间线、空间规则、制度、禁忌与边界。",
        "template": (
            "# 世界观设定\n\n"
            "## 基本规则\n待补充。\n\n"
            "## 时间线/空间规则\n待补充。\n\n"
            "## 禁忌与边界\n待补充。\n"
        ),
    },
    "plot": {
        "label": "情节设定",
        "path": "规划/情节设定.md",
        "aliases": ["planning/情节设定.md", "情节设定.md", "beat_sheet.md", "剧情设定.md", "伏笔表.md"],
        "description": "主线推进、关键冲突、节拍、伏笔与回收计划。",
        "template": (
            "# 情节设定\n\n"
            "## 主线推进\n待补充。\n\n"
            "## 关键冲突\n待补充。\n\n"
            "## 伏笔与回收\n待补充。\n"
        ),
    },
    "outline": {
        "label": "大纲",
        "path": "规划/大纲.md",
        "aliases": ["planning/大纲.md", "大纲.md", "outline.md", "高中卷大纲.md", "全书大纲.md", "章节大纲.md"],
        "description": "全书结构、分卷结构、章节大纲与章节钩子。新项目使用通用名 大纲.md；旧项目可通过 Wiki 映射到已有文件。",
        "template": "# 大纲\n\n## 全书结构\n待补充。\n\n## 第1章\n待补充。\n",
    },
    "chapter_summary": {
        "label": "已完成章节摘要",
        "path": "记忆/已完成章节摘要.md",
        "aliases": ["memory/已完成章节摘要.md", "已完成章节摘要.md"],
        "description": "确认/归档后的章节摘要与跨章节连续性记忆。",
        "template": "# 已完成章节摘要\n\n",
    },
    "chapter_status": {
        "label": "章节完成状态",
        "path": "记忆/章节完成状态.json",
        "aliases": ["memory/章节完成状态.json", "章节完成状态.json"],
        "description": "章节正文确认、留档和完成进度状态。",
        "template": "{}\n",
    },
    "narrative_rules": {
        "label": "叙事规则",
        "path": "规划/叙事规则.md",
        "aliases": ["planning/叙事规则.md", "双线叙事规则.md", "叙事规则.md"],
        "description": "项目特有的叙事结构、双线规则和章节组织原则。",
        "template": "# 叙事规则\n\n待补充。\n",
    },
    "style_guide": {
        "label": "风格规范",
        "path": "风格/风格规范.md",
        "aliases": ["style/风格规范.md", "校园风格写作技巧.md", "风格规范.md", "写作技巧.md"],
        "description": "语言风格、场景质感、校园生活细节和写作技巧规范。",
        "template": "# 风格规范\n\n待补充。\n",
    },
}

NOVEL_STRUCTURAL_DIRS = {
    "settings": {"path": "设定", "description": "基础设定、世界观、规则等设定层文件。"},
    "planning": {"path": "规划", "description": "大纲、情节、节拍、伏笔等规划层文件。"},
    "characters": {"path": "人物", "description": "人物档案、人物关系和角色声音材料。"},
    "memory": {"path": "记忆", "description": "章节摘要、完成状态和连续性记忆。"},
    "style": {"path": "风格", "description": "文风、类型、语言和审美规范。"},
    "chapters": {"path": "正文", "description": "确认归档后的章节正文。"},
    "outputs": {"path": "输出", "description": "材料包、快照、备份和生成产物。"},
    "accepted_planning": {"path": "输出/已采纳规划", "description": "用户确认后的规划类产物快照。"},
    "outline_backups": {"path": "输出/大纲备份", "description": "大纲覆盖写回前的自动备份。"},
    "invocation_logs": {"path": "日志/调用记录", "description": "创作任务 invocation、节点轨迹、成本和 provider 日志。"},
    "skills": {"path": "技能", "description": "项目私有技能。公共技能由类型级技能库提供。"},
    "wiki": {"path": "维基", "description": "项目级 Wiki，保存结构索引、稳定规则与项目共识。"},
    "references": {"path": "资产/参考材料", "description": "项目专用参考材料。"},
}

SHORT_FILM_STRUCTURAL_DOCS = {
    "brief": {
        "label": "创作简报",
        "path": "简报/创作简报.md",
        "aliases": ["brief/创作简报.md", "brief.md", "项目简报.md"],
        "description": "片长、核心命题、目标观众、制作限制与整体创作边界。",
        "template": "# 创作简报\n\n- 类型：电影短片\n- 片长：待定\n- 核心命题：待补充\n- 目标观众：待补充\n",
    },
    "concept": {
        "label": "概念",
        "path": "开发/概念.md",
        "aliases": ["development/概念.md", "logline.md", "概念.md"],
        "description": "短片概念、logline、主题、主角欲望、选择代价和结尾余味。",
        "template": "# 概念\n\n待补充。\n",
    },
    "character": {
        "label": "角色表",
        "path": "人物/角色表.md",
        "aliases": ["characters/角色表.md", "characters.md", "角色表.md", "人物档案.md"],
        "description": "角色欲望、阻碍、关系、转变和可拍摄行为。",
        "template": "# 角色表\n\n## 主角\n- 姓名：待定\n- 欲望：待定\n- 阻碍：待定\n- 转变：待定\n",
    },
    "beat_sheet": {
        "label": "节拍表",
        "path": "开发/节拍表.md",
        "aliases": ["development/节拍表.md", "beat_sheet.md", "节拍表.md"],
        "description": "短片节拍、冲突、转折和画面推进。",
        "template": "# 节拍表\n\n1. 开场画面\n2. 触发事件\n3. 选择与代价\n4. 反转/揭示\n5. 结尾余味\n",
    },
    "screenplay": {
        "label": "剧本",
        "path": "剧本/剧本.md",
        "aliases": ["script/剧本.md", "screenplay.md", "script.md", "剧本.md"],
        "description": "影视剧本正文，包含场景标题、动作、角色和对白。",
        "template": "# 剧本\n\n```text\n片名：待定\n\n1. INT./EXT. 场景 - 时间\n\n动作描写。\n\n角色\n对白。\n```\n",
    },
    "shot_list": {
        "label": "分镜表",
        "path": "分镜/分镜表.md",
        "aliases": ["storyboard/分镜表.md", "shot_list.md", "分镜表.md"],
        "description": "镜号、景别、画面、声音、备注和拍摄提示。",
        "template": "# 分镜表\n\n| 镜号 | 景别 | 画面 | 声音 | 备注 |\n| --- | --- | --- | --- | --- |\n",
    },
    "style": {
        "label": "影像风格",
        "path": "风格/影像风格.md",
        "aliases": ["style/影像风格.md", "style.md", "影像风格.md"],
        "description": "视觉基调、声音策略、剪辑节奏和画面风格。",
        "template": "# 影像风格\n\n- 视觉基调：待定\n- 声音策略：待定\n- 剪辑节奏：待定\n",
    },
}

CASUAL_STRUCTURAL_DOCS = {
    "inbox": {
        "label": "随想收集",
        "path": "随想/随想收集.md",
        "aliases": ["notes/随想收集.md", "materials.md", "随想.md", "灵感.md"],
        "description": "随想随记、零散念头、观察片段和临时记录的入口。",
        "template": "# 随想收集\n\n待补充。\n",
    },
    "ideas": {
        "label": "灵感池",
        "path": "灵感/灵感池.md",
        "aliases": ["ideas/灵感池.md", "outline.md", "结构.md", "灵感池.md"],
        "description": "可继续发展的主题、结构、问题、标题和方向。",
        "template": "# 灵感池\n\n待补充。\n",
    },
    "draft": {
        "label": "草稿",
        "path": "草稿/草稿.md",
        "aliases": ["drafts/草稿.md", "draft.md", "草稿.md"],
        "description": "由随想扩展出的草稿、段落和阶段性文本。",
        "template": "# 草稿\n\n",
    },
    "references": {
        "label": "参考材料",
        "path": "参考材料/参考材料.md",
        "aliases": ["references/参考材料.md", "参考材料.md", "素材.md"],
        "description": "摘录、链接、素材和可复用资料。",
        "template": "# 参考材料\n\n待补充。\n",
    },
}


ROLE_BY_TASK = {
    "logline": "base_setting",
    "brief": "base_setting",
    "materials": "base_setting",
    "generic": "base_setting",
    "setting": "base_setting",
    "world": "worldview",
    "worldview": "worldview",
    "character": "character",
    "characters": "character",
    "beat_sheet": "plot",
    "plot": "plot",
    "outline": "outline",
    "screenplay": "screenplay",
    "shot_list": "shot_list",
    "draft": "draft",
}

SHORT_FILM_TASK_ROLES = {
    "brief": "brief",
    "logline": "concept",
    "concept": "concept",
    "character": "character",
    "characters": "character",
    "beat_sheet": "beat_sheet",
    "outline": "beat_sheet",
    "screenplay": "screenplay",
    "script": "screenplay",
    "prose": "screenplay",
    "shot_list": "shot_list",
    "storyboard": "shot_list",
    "style": "style",
}

CASUAL_TASK_ROLES = {
    "outline": "ideas",
    "materials": "inbox",
    "generic": "inbox",
    "idea": "ideas",
    "ideas": "ideas",
    "draft": "draft",
    "prose": "draft",
    "fix": "draft",
    "reference": "references",
    "references": "references",
}


def structure_wiki_json(novel_id: str | None) -> Path:
    return wiki_dir(normalize_novel_id(novel_id)) / "project-structure.json"


def structure_wiki_md(novel_id: str | None) -> Path:
    return wiki_dir(normalize_novel_id(novel_id)) / "项目结构.md"


def ensure_project_structure_wiki(
    novel_id: str | None,
    *,
    project_kind: str | None = None,
    create_missing: bool = True,
) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    kind = project_kind or _project_kind_for_structure(nid)
    base = novel_dir(nid)
    base.mkdir(parents=True, exist_ok=True)
    doc_specs = _docs_for_kind(kind)
    dir_specs = _dirs_for_kind(kind)
    docs: dict[str, Any] = {}
    created: list[Path] = []
    for role, spec in doc_specs.items():
        chosen = _choose_existing(base, spec["path"], spec.get("aliases") or [])
        if chosen is None:
            chosen = base / spec["path"]
            if create_missing:
                _write_missing(chosen, spec["template"])
                created.append(chosen)
        docs[role] = {
            "role": role,
            "label": spec["label"],
            "path": _project_rel(chosen, base),
            "canonical_path": spec["path"],
            "aliases": spec.get("aliases") or [],
            "description": spec["description"],
        }
    dirs: dict[str, Any] = {}
    for role, spec in dir_specs.items():
        target = base / spec["path"]
        if create_missing:
            target.mkdir(parents=True, exist_ok=True)
            gitkeep = target / ".gitkeep"
            if not any(target.iterdir()) and not gitkeep.exists():
                gitkeep.write_text("", encoding="utf-8")
                created.append(gitkeep)
        dirs[role] = {
            "role": role,
            "path": spec["path"],
            "description": spec["description"],
        }
    wiki = {
        "version": STRUCTURE_VERSION,
        "project_id": nid,
        "project_kind": kind,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "documents": docs,
        "directories": dirs,
        "routing_notes": [
            "系统写入结构性材料时，应先按 role 查询本文件，再按 aliases/文件名相似度回退。",
            "历史项目迁移后，旧文件名会保留在 aliases 中，由本 Wiki 路由到新规范路径。",
            "覆盖式修改属于高风险写入，必须经用户显式确认后执行。",
        ],
    }
    _atomic_json(structure_wiki_json(nid), wiki)
    _write_structure_markdown(structure_wiki_md(nid), wiki)
    return {"ok": True, "novel_id": nid, "wiki": _rel(structure_wiki_json(nid)), "created": [_rel(p) for p in created]}


def load_project_structure(novel_id: str | None) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    path = structure_wiki_json(nid)
    detected_kind = _project_kind_for_structure(nid)
    if not path.exists():
        ensure_project_structure_wiki(nid, project_kind=detected_kind)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("documents"):
            if detected_kind != "generic" and data.get("project_kind") != detected_kind:
                ensure_project_structure_wiki(nid, project_kind=detected_kind)
                data = json.loads(path.read_text(encoding="utf-8"))
            return data
    except Exception:
        pass
    ensure_project_structure_wiki(nid, project_kind=detected_kind)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _project_kind_for_structure(novel_id: str | None) -> str:
    try:
        from app.project_kinds import project_kind as detect_project_kind

        return detect_project_kind(novel_id)
    except Exception:
        return "generic"


def role_for_task(task: str, project_kind: str | None = None) -> str | None:
    raw = str(task or "")
    if project_kind == "short_film":
        return SHORT_FILM_TASK_ROLES.get(raw) or ROLE_BY_TASK.get(raw)
    if project_kind == "generic":
        return CASUAL_TASK_ROLES.get(raw) or ROLE_BY_TASK.get(raw)
    return ROLE_BY_TASK.get(raw)


def task_for_role(role: str) -> str | None:
    return {
        "base_setting": "setting",
        "character": "character",
        "worldview": "world",
        "plot": "beat_sheet",
        "outline": "outline",
        "brief": "brief",
        "concept": "logline",
        "beat_sheet": "beat_sheet",
        "screenplay": "screenplay",
        "shot_list": "shot_list",
        "style": "style",
        "inbox": "materials",
        "ideas": "outline",
        "draft": "draft",
        "references": "materials",
    }.get(role)


def structure_prompt_context(novel_id: str | None, project_kind: str | None = None) -> dict[str, Any]:
    """Return compact project-structure options for LLM routing prompts.

    This is the generic contract for intent analysis: prompts should ask the LLM
    to choose from structure roles/paths instead of hard-coded novel-only names.
    """
    structure = load_project_structure(novel_id)
    kind = project_kind or structure.get("project_kind") or "generic"
    docs = structure.get("documents") or {}
    items = []
    for role, spec in docs.items():
        items.append({
            "role": role,
            "label": spec.get("label") or role,
            "path": spec.get("path") or "",
            "aliases": (spec.get("aliases") or [])[:5],
            "description": spec.get("description") or "",
        })
    task_roles = {}
    for task in [
        "logline", "setting", "world", "character", "beat_sheet", "outline",
        "prose", "expansion", "fix", "screenplay", "shot_list", "storyboard",
        "materials", "generic", "draft", "references",
    ]:
        role = role_for_task(task, kind)
        if role:
            task_roles[task] = role
    return {
        "project_kind": kind,
        "documents": items,
        "task_roles": task_roles,
        "target_paths": [item["path"] for item in items if item.get("path")],
        "target_labels": [item["label"] for item in items if item.get("label")],
    }


def _docs_for_kind(project_kind: str) -> dict[str, dict[str, Any]]:
    if project_kind == "short_film":
        return SHORT_FILM_STRUCTURAL_DOCS
    if project_kind == "generic":
        return CASUAL_STRUCTURAL_DOCS
    return NOVEL_STRUCTURAL_DOCS


def _dirs_for_kind(project_kind: str) -> dict[str, dict[str, str]]:
    if project_kind == "short_film":
        return {
            "brief": {"path": "简报", "description": "项目简报与制作限制。"},
            "development": {"path": "开发", "description": "概念、节拍和开发材料。"},
            "characters": {"path": "人物", "description": "角色设定和角色视觉资产。"},
            "script": {"path": "剧本", "description": "剧本正文和版本。"},
            "storyboard": {"path": "分镜", "description": "分镜表和镜头设计。"},
            "storyboards": {"path": "分镜生成", "description": "分镜提示词、生图和镜头资产。"},
            "style": {"path": "风格", "description": "影像风格、声音和剪辑策略。"},
            "assets": {"path": "资产", "description": "角色图、参考图和生成图素材。"},
            "outputs": {"path": "输出", "description": "确认稿、快照和生成物。"},
            "skills": {"path": "技能", "description": "项目私有技能。"},
            "wiki": {"path": "维基", "description": "项目级 Wiki。"},
        }
    if project_kind == "generic":
        return {
            "notes": {"path": "随想", "description": "随想随记和灵感入口。"},
            "ideas": {"path": "灵感", "description": "可发展的主题、问题和结构。"},
            "drafts": {"path": "草稿", "description": "草稿和阶段性文本。"},
            "references": {"path": "参考材料", "description": "参考材料和素材。"},
            "outputs": {"path": "输出", "description": "确认稿、快照和生成物。"},
            "skills": {"path": "技能", "description": "项目私有技能。"},
            "wiki": {"path": "维基", "description": "项目级 Wiki。"},
        }
    return NOVEL_STRUCTURAL_DIRS


def resolve_structure_target(
    novel_id: str | None,
    target: str,
    *,
    create_missing: bool = True,
) -> tuple[str | None, Path | None]:
    """Resolve a role, task, alias, or rough filename to the project file."""
    nid = normalize_novel_id(novel_id)
    base = novel_dir(nid)
    structure = load_project_structure(nid)
    docs = structure.get("documents") or {}
    role = _normalize_role_or_target(target, docs)
    if role and role in docs:
        path = base / docs[role]["path"]
        if create_missing and not path.exists():
            project_kind = structure.get("project_kind") or "novel_strong"
            template = _docs_for_kind(project_kind).get(role, {}).get("template", "")
            _write_missing(path, template)
        return role, path
    matched = find_related_structure_file(nid, target)
    if matched:
        role, path = matched
        return role, path
    return None, None


def find_related_structure_file(novel_id: str | None, target: str) -> tuple[str, Path] | None:
    nid = normalize_novel_id(novel_id)
    base = novel_dir(nid)
    docs = (load_project_structure(nid).get("documents") or {})
    needle = _norm_name(target)
    candidates: list[tuple[int, str, Path]] = []
    for role, spec in docs.items():
        names = [spec.get("path", ""), spec.get("canonical_path", ""), *(spec.get("aliases") or []), spec.get("label", "")]
        path = base / (spec.get("path") or "")
        for name in names:
            score = _name_score(needle, _norm_name(name))
            if score > 0:
                candidates.append((score, role, path))
    for path in base.glob("*.md"):
        score = _name_score(needle, _norm_name(path.name))
        if score > 0:
            role = _guess_role(path.name) or "base_setting"
            candidates.append((score, role, path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, role, path = candidates[0]
    return role, path


def structural_target_names(novel_id: str | None) -> set[str]:
    docs = (load_project_structure(novel_id).get("documents") or {})
    names: set[str] = set()
    for spec in docs.values():
        names.add(Path(spec.get("path") or "").name)
        names.add(Path(spec.get("canonical_path") or "").name)
        for alias in spec.get("aliases") or []:
            names.add(Path(alias).name)
    return {name for name in names if name}


def _choose_existing(base: Path, canonical: str, aliases: list[str]) -> Path | None:
    for name in [canonical, *aliases]:
        path = base / name
        if path.is_file():
            return path
    stem = Path(canonical).stem
    matches = sorted(base.glob(f"*{stem}*.md"))
    return matches[0] if matches else None


def _normalize_role_or_target(target: str, docs: dict[str, Any]) -> str | None:
    raw = str(target or "").strip()
    if raw in docs:
        return raw
    for task_roles in (SHORT_FILM_TASK_ROLES, CASUAL_TASK_ROLES, ROLE_BY_TASK):
        mapped = task_roles.get(raw)
        if mapped and mapped in docs:
            return mapped
    raw_name = Path(raw).name
    for role, spec in docs.items():
        names = {spec.get("path") or "", spec.get("canonical_path") or ""}
        names.update(spec.get("aliases") or [])
        basename_matches = {Path(name).name for name in names if name}
        if raw in names or raw_name in basename_matches:
            return role
    guessed = _guess_role(raw, docs)
    if guessed and guessed in docs:
        return guessed
    return None


def _guess_role(value: str, docs: dict[str, Any] | None = None) -> str | None:
    text = str(value or "")
    available = set((docs or {}).keys())

    def pick(*roles: str) -> str | None:
        if not available:
            return roles[0] if roles else None
        for role in roles:
            if role in available:
                return role
        return None

    if any(word in text for word in ["人物", "角色", "character"]):
        return pick("character")
    if any(word in text for word in ["世界观", "设定及问题", "穿越", "world"]):
        return pick("worldview", "brief", "inbox")
    if any(word in text for word in ["情节", "剧情", "节拍", "伏笔", "beat", "plot"]):
        return pick("plot", "beat_sheet", "ideas")
    if any(word in text for word in ["大纲", "outline"]):
        return pick("outline", "beat_sheet", "ideas")
    if any(word in text for word in ["剧本", "screenplay", "script"]):
        return pick("screenplay", "draft")
    if any(word in text for word in ["分镜", "shot", "storyboard"]):
        return pick("shot_list")
    if any(word in text for word in ["风格", "style", "影像"]):
        return pick("style", "style_guide")
    if any(word in text for word in ["概念", "logline", "主题", "立意"]):
        return pick("concept", "base_setting", "ideas")
    if any(word in text for word in ["基础", "简报", "brief"]):
        return pick("base_setting", "brief", "inbox")
    if any(word in text for word in ["随想", "灵感", "材料", "素材", "materials"]):
        return pick("inbox", "ideas", "references", "base_setting")
    if any(word in text for word in ["草稿", "draft"]):
        return pick("draft", "screenplay")
    if any(word in text for word in ["参考", "reference"]):
        return pick("references")
    return None


def _name_score(a: str, b: str) -> int:
    if not a or not b:
        return 0
    if a == b:
        return 100
    if a in b or b in a:
        return 80
    overlap = set(a) & set(b)
    return len(overlap) if len(overlap) >= 2 else 0


def _norm_name(value: str) -> str:
    return re.sub(r"[\s_\-./\\]+", "", Path(str(value or "")).name.lower())


def _write_missing(path: Path, content: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _write_structure_markdown(path: Path, data: dict[str, Any]) -> None:
    lines = [
        "# 项目结构 Wiki",
        "",
        "本文件记录项目结构性目录和文件的用途。系统执行归档、更新和材料路由时优先读取 `project-structure.json`。",
        "",
        "## 结构文件",
        "",
        "| 角色 | 文件 | 用途 | 别名/历史名 |",
        "| --- | --- | --- | --- |",
    ]
    for item in (data.get("documents") or {}).values():
        lines.append(
            f"| {item.get('label')} | `{item.get('path')}` | {item.get('description')} | "
            f"{', '.join(f'`{x}`' for x in item.get('aliases') or [])} |"
        )
    lines.extend(["", "## 结构目录", "", "| 角色 | 目录 | 用途 |", "| --- | --- | --- |"])
    for item in (data.get("directories") or {}).values():
        lines.append(f"| {item.get('role')} | `{item.get('path')}` | {item.get('description')} |")
    lines.extend(["", "## 路由说明", ""])
    for note in data.get("routing_notes") or []:
        lines.append(f"- {note}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _project_rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base)).replace("\\", "/")
    except ValueError:
        return path.name


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
