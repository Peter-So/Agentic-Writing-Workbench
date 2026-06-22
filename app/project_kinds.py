from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT, load_runtime_config
from app.llm_client import create_llm, resolve_text_model
from app.novel_context import novel_dir, normalize_novel_id


DEFAULT_KIND = "generic"
STRONG_NOVEL_KIND = "novel_strong"
SHORT_FILM_KIND = "short_film"


KIND_LABELS = {
    DEFAULT_KIND: "随想项目",
    STRONG_NOVEL_KIND: "强规范小说工程",
    SHORT_FILM_KIND: "电影短片脚本项目",
}


def project_meta_path(novel_id: str | None) -> Path:
    return novel_dir(novel_id) / "project.yaml"


def load_project_meta(novel_id: str | None) -> dict[str, Any]:
    path = project_meta_path(novel_id)
    if not path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def project_kind(novel_id: str | None) -> str:
    nid = normalize_novel_id(novel_id)
    meta = load_project_meta(nid)
    kind = (meta.get("type") or meta.get("kind") or "").strip()
    if kind:
        return kind
    path = novel_dir(nid)
    if (
        ((path / "脚本" / "material_assembler.py").exists() or (path / "scripts" / "material_assembler.py").exists())
        and ((path / "提示词").exists() or (path / "prompts").exists())
    ):
        return STRONG_NOVEL_KIND
    if (
        (path / "script.md").exists()
        or (path / "screenplay.md").exists()
        or (path / "script" / "剧本.md").exists()
        or (path / "development" / "节拍表.md").exists()
        or (path / "剧本" / "剧本.md").exists()
        or (path / "开发" / "节拍表.md").exists()
    ):
        return SHORT_FILM_KIND
    return DEFAULT_KIND


def ensure_project_initialized(novel_id: str | None, user_message: str = "",
                               requested_kind: str | None = None) -> dict[str, Any]:
    """Create a fitting directory skeleton for empty/lightweight projects.

    Existing projects are not overwritten. Strong 001-style projects keep their
    own scripts. Empty projects get a casual-note or short-film structure based on
    the user's request.
    """
    nid = normalize_novel_id(novel_id)
    path = novel_dir(nid)
    path.mkdir(parents=True, exist_ok=True)
    current_kind = project_kind(nid)
    existing = [p for p in path.iterdir()] if path.exists() else []
    if existing and current_kind == STRONG_NOVEL_KIND:
        try:
            from app.novel_artifacts import ensure_novel_files

            ensured = ensure_novel_files(nid)
            return {
                "ok": True,
                "created": bool(ensured.get("created")),
                "kind": current_kind,
                "path": _rel(path),
                "files": ensured.get("created") or [],
            }
        except Exception:
            return {"ok": True, "created": False, "kind": current_kind, "path": _rel(path)}
    if existing and current_kind != DEFAULT_KIND:
        return {"ok": True, "created": False, "kind": current_kind, "path": _rel(path)}
    kind = requested_kind or infer_project_kind(user_message, nid)
    if current_kind == STRONG_NOVEL_KIND:
        kind = current_kind
    if kind == SHORT_FILM_KIND:
        created = _init_short_film(path, nid)
    elif kind == STRONG_NOVEL_KIND:
        created = _init_novel_project(path, nid)
    else:
        created = _init_generic(path, nid)
        kind = DEFAULT_KIND
    return {"ok": True, "created": bool(created), "kind": kind, "path": _rel(path), "files": [_rel(p) for p in created]}


def create_project(novel_id: str, kind: str) -> dict[str, Any]:
    nid = normalize_novel_id(novel_id)
    if kind not in {STRONG_NOVEL_KIND, SHORT_FILM_KIND, DEFAULT_KIND}:
        raise ValueError("不支持的项目类型")
    path = novel_dir(nid)
    if path.exists() and any(path.iterdir()):
        return {"ok": False, "exists": True, "message": f"项目 {nid} 已存在"}
    path.mkdir(parents=True, exist_ok=True)
    return ensure_project_initialized(nid, requested_kind=kind)


def infer_project_kind(user_message: str, novel_id: str | None = None) -> str:
    text = (user_message or "").lower()
    if any(word in text for word in ["电影", "短片", "剧本", "分镜", "screenplay", "film", "镜头"]):
        return SHORT_FILM_KIND
    if any(word in text for word in ["小说", "章节", "人物档案", "大纲", "正文"]):
        return STRONG_NOVEL_KIND if project_kind(novel_id) == STRONG_NOVEL_KIND else DEFAULT_KIND
    try:
        cfg = load_runtime_config()
        model_key = resolve_text_model(cfg, "chat")
        llm = create_llm(cfg, model_key, temperature=0, max_tokens=80)
        prompt = (
            "判断用户要创建/创作的项目类型，只输出 JSON："
            "{\"kind\":\"short_film|generic\"}。generic 表示随想、随记、灵感类项目。\n"
            f"用户请求：{user_message[:800]}"
        )
        raw = getattr(llm.invoke(prompt), "content", "") or ""
        data = _parse_json(raw)
        kind = (data or {}).get("kind")
        return kind if kind in {SHORT_FILM_KIND, DEFAULT_KIND} else DEFAULT_KIND
    except Exception:
        return DEFAULT_KIND


def assemble_generic_bundle(novel_id: str | None, query: str, task: str = "prose",
                            chapter: int | None = None) -> dict[str, Any]:
    from app.writing_sop import sop_for_task

    nid = normalize_novel_id(novel_id)
    kind = project_kind(nid)
    path = novel_dir(nid)
    docs = _read_project_docs(path)
    spec = _generic_spec(kind)
    workflow_sop = sop_for_task(kind, task)
    bundle = {
        "task": task,
        "chapter": chapter,
        "novel_id": nid,
        "project_kind": kind,
        "materials": {
            "project_docs": docs,
            "chapter_outline": _doc_by_roles(docs, ["outline", "beat_sheet", "ideas"]),
            "character_profiles": _doc_by_roles(docs, ["character"]),
            "worldbuilding": _doc_by_roles(docs, ["worldview", "brief"]),
            "plot_notes": _doc_by_roles(docs, ["plot", "beat_sheet", "ideas"]),
            "constraints": _doc_by_roles(docs, ["base_setting", "brief", "style", "inbox"]),
        },
        "spec": spec,
        "recompose_instruction": _generic_instruction(kind, task),
        "workflow_sop": workflow_sop,
    }
    request_text = "\n\n".join([
        f"# {KIND_LABELS.get(kind, kind)}创作请求",
        f"任务：{task}",
        f"章节/场次：{chapter or '未指定'}",
        f"用户要求：{query}",
        "## 项目材料",
        "\n\n".join(f"### {name}\n{text}" for name, text in docs.items()) or "暂无项目材料。",
        "## 规则",
        spec,
    ])
    bundle["request_text"] = request_text
    out_dir = path / "输出"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = out_dir / f"material_bundle_{task}_{stamp}.json"
    tmp = output.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, output)
    return {
        "ok": True,
        "novel_id": nid,
        "project_kind": kind,
        "task": task,
        "chapter": chapter,
        "output_path": _rel(output),
        "bundle": bundle,
        "log": "generic_branch",
        "cached": False,
    }


def _init_short_film(path: Path, project_id: str) -> list[Path]:
    files = {
        "project.yaml": f"project_id: {project_id}\ntype: short_film\nname: 电影短片脚本项目 {project_id}\ncreated_at: {datetime.now().isoformat(timespec='seconds')}\n",
        "简报/创作简报.md": "# 创作简报\n\n- 类型：电影短片\n- 片长：待定\n- 核心命题：待补充\n- 目标观众：待补充\n",
        "开发/概念.md": "# 概念\n\n待补充。\n",
        "人物/角色表.md": "# 角色表\n\n## 主角\n- 姓名：待定\n- 欲望：待定\n- 阻碍：待定\n- 转变：待定\n",
        "开发/节拍表.md": "# 节拍表\n\n1. 开场画面\n2. 触发事件\n3. 选择与代价\n4. 反转/揭示\n5. 结尾余味\n",
        "剧本/剧本.md": "# 剧本\n\n```text\n片名：待定\n\n1. INT./EXT. 场景 - 时间\n\n动作描写。\n\n角色\n对白。\n```\n",
        "分镜/分镜表.md": "# 分镜表\n\n| 镜号 | 景别 | 画面 | 声音 | 备注 |\n| --- | --- | --- | --- | --- |\n",
        "风格/影像风格.md": "# 影像风格\n\n- 视觉基调：待定\n- 声音策略：待定\n- 剪辑节奏：待定\n",
        "输出/.gitkeep": "",
        "资产/人物/.gitkeep": "",
        "资产/图片/.gitkeep": "",
    }
    created = _write_missing(path, files)
    try:
        from app.project_structure import ensure_project_structure_wiki
        result = ensure_project_structure_wiki(project_id, project_kind=SHORT_FILM_KIND, create_missing=True)
        created.extend(ROOT / rel for rel in result.get("created") or [] if rel)
    except Exception:
        pass
    return created


def _init_novel_project(path: Path, project_id: str) -> list[Path]:
    files = {
        "project.yaml": f"project_id: {project_id}\ntype: novel_strong\nname: 小说项目 {project_id}\ncreated_at: {datetime.now().isoformat(timespec='seconds')}\n",
        "设定/基础设定.md": "# 基础设定\n\n## 项目一句话\n待补充。\n\n## 类型与基调\n待补充。\n\n## 核心命题\n待补充。\n\n## 创作约束\n待补充。\n",
        "人物/人物档案.md": "# 人物档案\n\n## 主角\n- 姓名：待定\n- 欲望：待定\n- 阻碍：待定\n- 转变：待定\n\n## 重要配角\n待补充。\n",
        "设定/世界观设定.md": "# 世界观设定\n\n## 基本规则\n待补充。\n\n## 时间线/空间规则\n待补充。\n\n## 禁忌与边界\n待补充。\n",
        "规划/情节设定.md": "# 情节设定\n\n## 主线推进\n待补充。\n\n## 关键冲突\n待补充。\n\n## 伏笔与回收\n待补充。\n",
        "规划/大纲.md": "# 大纲\n\n## 全书结构\n待补充。\n\n## 第1章\n待补充。\n",
        "记忆/已完成章节摘要.md": "# 已完成章节摘要\n\n",
        "记忆/章节完成状态.json": "{}\n",
        "设定/.gitkeep": "",
        "规划/.gitkeep": "",
        "人物/.gitkeep": "",
        "记忆/.gitkeep": "",
        "风格/.gitkeep": "",
        "正文/.gitkeep": "",
        "输出/.gitkeep": "",
        "输出/已采纳规划/.gitkeep": "",
        "输出/大纲备份/.gitkeep": "",
        "日志/调用记录/.gitkeep": "",
        "技能/.gitkeep": "",
        "维基/.gitkeep": "",
        "资产/参考材料/.gitkeep": "",
    }
    created = _write_missing(path, files)
    try:
        from app.project_structure import ensure_project_structure_wiki

        result = ensure_project_structure_wiki(project_id, project_kind=STRONG_NOVEL_KIND, create_missing=True)
        created.extend(ROOT / rel for rel in result.get("created") or [] if rel)
    except Exception:
        pass
    return created


def _init_generic(path: Path, project_id: str) -> list[Path]:
    files = {
        "project.yaml": f"project_id: {project_id}\ntype: generic\nname: 随想项目 {project_id}\ncreated_at: {datetime.now().isoformat(timespec='seconds')}\n",
        "随想/随想收集.md": "# 随想收集\n\n待补充。\n",
        "灵感/灵感池.md": "# 灵感池\n\n待补充。\n",
        "草稿/草稿.md": "# 草稿\n\n",
        "参考材料/参考材料.md": "# 参考材料\n\n待补充。\n",
        "输出/.gitkeep": "",
    }
    created = _write_missing(path, files)
    try:
        from app.project_structure import ensure_project_structure_wiki
        result = ensure_project_structure_wiki(project_id, project_kind=DEFAULT_KIND, create_missing=True)
        created.extend(ROOT / rel for rel in result.get("created") or [] if rel)
    except Exception:
        pass
    return created


def _write_missing(path: Path, files: dict[str, str]) -> list[Path]:
    created: list[Path] = []
    for name, content in files.items():
        target = path / name
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created.append(target)
    return created


def _read_project_docs(path: Path) -> dict[str, str]:
    names = [
        "brief.md", "logline.md", "characters.md", "beat_sheet.md", "screenplay.md",
        "shot_list.md", "style.md", "outline.md", "materials.md", "draft.md",
        "设定/基础设定.md", "设定/世界观设定.md",
        "规划/情节设定.md", "规划/大纲.md",
        "人物/人物档案.md", "记忆/已完成章节摘要.md",
        "settings/基础设定.md", "settings/世界观设定.md",
        "planning/情节设定.md", "planning/大纲.md",
        "characters/人物档案.md", "memory/已完成章节摘要.md",
        "基础设定.md", "世界观设定.md", "情节设定.md",
        "大纲.md",
        "人物档案.md",
        "维基/项目结构.md",
        "技能/lessons_skill.md",
        "wiki/项目结构.md",
        "skills/lessons_skill.md",
    ]
    try:
        from app.project_structure import load_project_structure

        structure = load_project_structure(path.name)
        role_by_rel = {}
        for item in (structure.get("documents") or {}).values():
            rel = item.get("path")
            if rel:
                role_by_rel[rel] = item.get("role") or ""
            if rel and rel not in names:
                names.append(rel)
    except Exception:
        role_by_rel = {}
    docs: dict[str, str] = {}
    for name in names:
        p = path / name
        if p.exists() and p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")[:12000]
            docs[name] = text
            role = role_by_rel.get(name)
            if role:
                docs[f"role:{role}"] = text
    return docs


def _doc_by_roles(docs: dict[str, str], roles: list[str]) -> str:
    for role in roles:
        text = docs.get(f"role:{role}", "")
        if text:
            return text
    return ""


def _generic_spec(kind: str) -> str:
    if kind == SHORT_FILM_KIND:
        return (
            "你是电影短片编剧。输出必须符合影视剧本/短片开发范式："
            "重视画面动作、场景、冲突、节拍、对白潜台词；避免小说式心理旁白。"
            "如写剧本，使用 场景标题 / 动作 / 角色 / 对白 的格式。"
        )
    if kind == STRONG_NOVEL_KIND:
        return (
            "你是中文小说创作编辑。输出必须符合小说创作范式："
            "重视人物欲望、冲突推进、场景细节、章节钩子和语言质感；避免空泛总结。"
        )
    return "你是随想项目整理助手。基于随想、灵感和参考材料，输出清晰、可继续沉淀或扩展的内容。"


def _generic_instruction(kind: str, task: str) -> str:
    if kind == SHORT_FILM_KIND:
        mapping = {
            "logline": "输出短片概念开发材料，包含片名方向、logline、主题、主角欲望、障碍、选择代价、结尾余味和可扩展成剧本的要点。",
            "character": "补全角色弧光、欲望、阻碍、关系和可拍摄行为。",
            "outline": "输出短片主题、三幕/五节拍结构和可扩展成剧本的概念依据。",
            "beat_sheet": "输出短片节拍表，每个节拍包含画面、冲突、转折。",
            "screenplay": "输出剧本正文，使用影视脚本格式，少旁白，多动作和对白。",
            "prose": "输出剧本正文，使用影视脚本格式，少旁白，多动作和对白。",
            "shot_list": "输出分镜/镜头表，包含镜号、景别、画面、声音、备注。",
            "fix": "按影视剧本标准修复节奏、对白、动作和场景逻辑。",
        }
        return mapping.get(task, "按电影短片项目范式输出。")
    if kind == STRONG_NOVEL_KIND:
        mapping = {
            "logline": "输出小说基础设定，包含题材、类型基调、核心命题、主角原型、主要矛盾和可扩展为大纲的材料。",
            "setting": "输出小说基础设定，包含题材、类型基调、核心命题、创作边界和必须延续的规则。",
            "world": "输出世界观设定，包含基本规则、时间线/空间规则、社会结构、禁忌边界和对人物行动的影响。",
            "character": "补全人物定位、欲望、阻碍、关系、弧光和声音。",
            "outline": "输出小说主线、阶段结构、关键事件和章节钩子。",
            "beat_sheet": "输出情节节拍，每个节拍包含冲突、压力、转折和余味。",
            "prose": "输出小说正文，使用场景、动作、对白和感官细节推进。",
            "expansion": "对指定薄弱处扩写，补足场景细节、人物动作和情绪层次。",
            "fix": "修复节奏、人物一致性、逻辑和语言问题。",
        }
        return mapping.get(task, "按中文小说项目范式输出。")
    return "按随想项目范式组织材料，保留灵感的开放性，同时输出可沉淀、可扩展、可编辑的结果。"


def _parse_json(raw: str) -> dict[str, Any] | None:
    import re

    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)
