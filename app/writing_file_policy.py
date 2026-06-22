from __future__ import annotations

from pathlib import Path
from typing import Any

from app.novel_context import WRITING_ROOT


FRAMEWORK_SAVE_DENY_EXACT = {
    "README.md",
    "AGENTS.md",
    "project.yaml",
    "writing_paths.py",
    "AGENT_WEB_PLAN.md",
    "ENGINEERING_OPTIMIZATION_PLAN.md",
    "HEALTH_CHECK_REPORT.md",
    "LANGGRAPH_OPTIMIZATION_PLAN.md",
    "MEMORY_ARCHITECTURE_PLAN.md",
    "MIGRATION_AUDIT.md",
    "PROVIDER_ASYNC_DISPLAY_PLAN.md",
    "RAG_INCREMENTAL_MEMORY_PLAN.md",
    "WRITING_DASHBOARD_PLAN.md",
}

FRAMEWORK_SAVE_DENY_DIRS = {
    "data",
    "lessons",
    "logs",
    "memory",
    "novel-acquisition",
    "novel-skill-suite",
    "references",
    "sop-definitions",
}

FRAMEWORK_SAVE_DENY_SUFFIX_PATTERNS = (
    "_AUDIT.md",
    "_PLAN.md",
    "_REPORT.md",
)

PROJECT_WIKI_PROTECTED_FILES = {
    "维基/README.md",
    "维基/index.json",
    "维基/project-structure.json",
    "维基/项目结构.md",
    "维基/project_wiki.json",
    "维基/project-structure-map.md",
    "wiki/README.md",
    "wiki/index.json",
    "wiki/project-structure.json",
    "wiki/项目结构.md",
    "wiki/project_wiki.json",
    "wiki/project-structure-map.md",
}


def path_policy(path: Path) -> dict[str, Any]:
    """Return Web file-editor policy for a resolved path under projects/writing."""
    rel = _relative(path)
    protected, reason = _protected_reason(rel)
    return {
        "editable": not protected,
        "protected": protected,
        "reason": reason,
        "rel_path": rel.as_posix(),
    }


def is_framework_file(path: Path) -> bool:
    return bool(path_policy(path)["protected"])


def editable_message(path: Path) -> str:
    policy = path_policy(path)
    if policy["editable"]:
        return "可编辑。"
    return f"框架文件受保护：{policy['reason']}。"


def _relative(path: Path) -> Path:
    resolved_root = WRITING_ROOT.resolve()
    resolved_path = path.resolve()
    return resolved_path.relative_to(resolved_root)


def _protected_reason(rel: Path) -> tuple[bool, str]:
    parts = rel.parts
    if not parts:
        return True, "writing 项目根目录不是作品内容文件"

    # novels/<id>/... 是作品空间；但项目 Wiki 目录整体由系统/API 管理。
    # 其中既有 Markdown 条目，也有 JSON 索引，普通 Web 文件编辑器只能只读查看。
    if len(parts) >= 2 and parts[0] == "novels":
        nested = Path(*parts[2:]).as_posix() if len(parts) > 2 else ""
        if nested == "维基" or nested.startswith("维基/") or nested == "wiki" or nested.startswith("wiki/"):
            return True, f"{nested} 属于项目 Wiki，只能通过专属 Wiki 页面只读查看"
        if nested in PROJECT_WIKI_PROTECTED_FILES:
            return True, f"{nested} 属于项目 Wiki 强制结构/索引文件"
        return False, ""

    first = parts[0]
    name = rel.name
    if first in FRAMEWORK_SAVE_DENY_DIRS:
        return True, f"{first} 属于框架/运行时目录"
    if len(parts) == 1 and name in FRAMEWORK_SAVE_DENY_EXACT:
        return True, f"{name} 属于项目框架说明或计划文件"
    if len(parts) == 1 and any(name.endswith(suffix) for suffix in FRAMEWORK_SAVE_DENY_SUFFIX_PATTERNS):
        return True, f"{name} 属于审计/计划/报告类框架文件"
    return False, ""
