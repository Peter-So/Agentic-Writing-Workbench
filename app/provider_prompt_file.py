from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import ROOT
from app.novel_context import WRITING_ROOT, normalize_novel_id
from app.project_paths import outputs_dir
from app.project_kinds import SHORT_FILM_KIND
from app.writing_sop import sop_for_task
from app.writing_harness import check_request_text, estimate_budget

# Provider 提问文件：把材料/规范/限制拼成"provider 可直接读"的完整中文 prompt，优先落盘到当前项目 输出/。
# 与 material_bundle.json（程序读的结构化数据）不同，这是给千问/豆包/DeepSeek 直接阅读作答的提问包。
# WRITING_OUTPUT_DIR 仅作为未指定项目时的测试/兜底目录。
FALLBACK_OUTPUT_DIR = Path(os.getenv("WRITING_OUTPUT_DIR") or (WRITING_ROOT / "outputs"))

# 各环节的任务说明（贴合 TASK_SPEC 的六类）。
_NOVEL_TASK_BRIEF = {
    "prose": "请创作本章【正文】，4000-6000 字。",
    "character": "请设计【人物设定】（姓名/定位/性格/声音/处境）。",
    "outline": "请产出【大纲】要点（事件骨架/时间锚/章末钩子）。",
    "beat_sheet": "请设计本章【情节】节奏（场景序列/压力源→延迟→释放→余味）。",
    "expansion": "请对指定薄弱处做【扩写】，补足感官细节与人物声音。",
    "fix": "请对指定问题段做【修复】，消除违规并保持人物一致。",
}

_SHORT_FILM_TASK_BRIEF = {
    "logline": "请产出电影短片【概念开发材料】，用于后续扩展成正式剧本；包含片名方向、logline、核心命题、主角欲望、障碍、选择代价、结尾余味与可拍摄展开要点。",
    "character": "请设计电影短片【角色】，包含欲望、阻碍、秘密、关系、弧光与可拍摄行为。",
    "outline": "请产出电影短片【结构大纲】，包含主题、三幕/五节拍与关键反转。",
    "beat_sheet": "请产出电影短片【节拍表】，每个节拍包含画面、冲突、转折和声音线索。",
    "screenplay": "请创作电影短片【剧本正文】，使用场景标题、动作、角色、对白格式。",
    "prose": "请创作电影短片【剧本正文】，使用场景标题、动作、角色、对白格式。",
    "shot_list": "请产出电影短片【分镜/镜头表】，包含镜号、景别、画面、声音、备注。",
    "fix": "请按电影短片标准【修订】节奏、对白、动作、冲突和可拍摄性。",
}

# 通用限制（从 04-chapter-prose-spec 的 MUST NOT 提炼，喂给 provider）。
_MUST_NOT = [
    "禁止“不是A是B”否定结构",
    "禁止叙事者翻译潜台词、命名心理机制",
    "禁止空洞情绪标签与比喻（像一束光/一种温暖的感觉）",
    "禁止段尾升华/总结/点明意义",
    "禁止 AI 身体怪癖（磨牙/咬指甲/转笔/抖腿）",
    "禁用词：标志着/象征着/不禁/不由得/仿佛/宛如/似乎/意味着",
    "禁止同章完全相同的句子",
    "每段须可追溯到材料来源",
]

_SHORT_FILM_MUST_NOT = [
    "禁止写成小说散文式长篇心理旁白",
    "禁止只讲道理不落到可拍摄动作、画面、声音",
    "禁止角色只表达情绪标签，必须通过行为和对白呈现",
    "禁止脱离项目简报、角色表、节拍表自由发挥",
    "如写剧本，必须使用 场景标题 / 动作 / 角色 / 对白 的影视脚本格式",
    "如写分镜，必须表格化或条目化，便于拍摄执行",
]


def _project_output_dir(bundle: dict[str, Any]) -> Path:
    nid = bundle.get("novel_id") or bundle.get("project_id")
    if nid:
        try:
            return outputs_dir(normalize_novel_id(str(nid)))
        except Exception:
            pass
    return FALLBACK_OUTPUT_DIR


def _project_label(bundle: dict[str, Any], chapter: int | None) -> str:
    nid = bundle.get("novel_id") or "未指定"
    kind = bundle.get("project_kind") or "novel_strong"
    unit = "场次/段落" if kind == SHORT_FILM_KIND else "章节"
    return f"项目：writing / {nid} / {kind}｜{unit}：{chapter or '未指定'}"


def _build_provider_material_index(
    *,
    task: str,
    bundle: dict[str, Any],
    chapter: int | None,
    message: str,
) -> dict[str, Any]:
    from app.chapter_material_index import build_chapter_material_index

    return build_chapter_material_index(
        novel_id=bundle.get("novel_id"),
        task=task,
        chapter=chapter,
        message=message,
        bundle=bundle,
    )


def _delivery_rules(project_kind: str, task: str) -> list[str]:
    if project_kind == SHORT_FILM_KIND:
        rules = [
            "直接输出可采用的成品内容，不要解释过程。",
            "每一段/每一项都要能回到项目材料或 provider 亮点，不要凭空扩张设定。",
            "剧本正文要重画面、动作、对白和声音；分镜要清晰可拍。",
        ]
        if task == "logline":
            rules.append("概念任务应输出多项结构化剧本开发材料；除非用户明确要求，不要压缩为单句极短版本。")
        return rules
    if task in {"prose", "expansion"}:
        return [
            "直接输出成品正文，不要解释过程。",
            "正文按章节叙事输出，字数 4000-6000。",
            "如需标注来源，只能使用外发材料名：[本章大纲]/[前情]/[人物设定]/[参考技法]。",
        ]
    return [
        "直接输出可采用的成品内容，不要解释过程。",
        "结构清晰，可直接进入后续归档或改写流程。",
    ]


def build_request_text(
    task: str,
    bundle: dict[str, Any],
    chapter: int | None,
    message: str,
) -> str:
    """把 bundle 的材料 + 规范 + 限制拼成 provider 可直接读的完整提问文本。"""
    project_kind = bundle.get("project_kind") or "novel_strong"
    from app.chapter_material_index import format_provider_packet

    material_index = _build_provider_material_index(
        task=task,
        bundle=bundle,
        chapter=chapter,
        message=message,
    )
    packet = material_index.get("external_packet") or {}
    material_block = format_provider_packet(material_index)
    task_sentence = packet.get("task") or message or "按当前材料完成本次写作任务。"
    if project_kind == SHORT_FILM_KIND:
        brief = _SHORT_FILM_TASK_BRIEF.get(task, "请完成下述电影短片创作任务。")
        must_not = _SHORT_FILM_MUST_NOT
    else:
        brief = _NOVEL_TASK_BRIEF.get(task, "请完成下述写作任务。")
        must_not = _MUST_NOT
    delivery_rules = _delivery_rules(project_kind, task)
    sections = [
        "# 写作外发任务单",
        _project_label(bundle, chapter),
        "",
        "## 一、任务与交付",
        f"- 任务：{task_sentence}",
        *(f"- 交付：{rule}" for rule in delivery_rules),
        "",
        "## 二、必用材料（请基于以下材料创作，不要脱离材料自由发挥）",
        material_block,
        "",
        "## 三、硬性限制（违反任一条即不合格）",
        "\n".join(f"- {x}" for x in must_not),
    ]
    return "\n".join(sections)


def write_request_file(task: str, bundle: dict[str, Any], chapter: int | None, message: str) -> dict[str, Any]:
    """生成提问文件并落盘到项目 输出/，返回路径与文本。"""
    text = build_request_text(task, bundle, chapter, message)
    workflow_sop = bundle.get("workflow_sop") or sop_for_task(bundle.get("project_kind"), task)
    harness = check_request_text(
        project_kind=bundle.get("project_kind"),
        task=task,
        request_text=text,
        workflow_sop=workflow_sop,
    )
    budget = estimate_budget(prompt_text=text)
    output_dir = _project_output_dir(bundle)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ch = f"unit{chapter:02d}" if chapter else "nounit"
    path = output_dir / f"{task}_request_{ch}_{stamp}.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    try:
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        rel = str(path)  # 兜底目录被测试重定向到 ROOT 外时退回绝对路径
    return {
        "ok": harness.get("ok", True),
        "path": rel,
        "text": text,
        "chars": len(text),
        "harness": harness,
        "budget": budget,
    }
