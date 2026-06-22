from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_ROOTS = [
    ROOT / "app",
    ROOT / "scripts",
    ROOT / "projects" / "writing",
]
EXTRA_TEXT_FILES = [
    ROOT / ".editorconfig",
    ROOT / "README.md",
]
TEXT_SUFFIXES = {
    ".css",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "backups",
    "chroma",
    "Image",
    "images",
    "node_modules",
    "outputs-corpus",
    "参考小说原文",
    "原文",
    "备份",
}
FRONTEND_INDEX = ROOT / "app" / "static-writing" / "index.html"
FRONTEND_ASSETS = ("app.js", "styles.css")
VERSION_RE = re.compile(r"/static/(?P<asset>app\.js|styles\.css)\?v=(?P<version>[0-9]{8}-[a-z0-9-]+)")


def main() -> int:
    issues: list[str] = []
    issues.extend(check_utf8_files())
    issues.extend(check_frontend_versions())
    if issues:
        print("Writing 项目规范检查失败：")
        for item in issues:
            print(f"- {item}")
        return 1
    print("Writing 项目规范检查通过：UTF-8 与前端版本号正常。")
    return 0


def check_utf8_files() -> list[str]:
    issues: list[str] = []
    for path in EXTRA_TEXT_FILES:
        if path.exists():
            issues.extend(_check_utf8_file(path))
    for root in TEXT_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if _should_skip(path):
                continue
            issues.extend(_check_utf8_file(path))
    return issues


def _check_utf8_file(path: Path) -> list[str]:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return [f"{_rel(path)} 使用了 UTF-8 BOM，请改为 UTF-8 无 BOM。"]
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"{_rel(path)} 不是有效 UTF-8：{exc}"]
    return []


def check_frontend_versions() -> list[str]:
    if not FRONTEND_INDEX.exists():
        return [f"{_rel(FRONTEND_INDEX)} 不存在。"]
    text = FRONTEND_INDEX.read_text(encoding="utf-8")
    versions = {match.group("asset"): match.group("version") for match in VERSION_RE.finditer(text)}
    issues = []
    for asset in FRONTEND_ASSETS:
        version = versions.get(asset)
        if not version:
            issues.append(f"app/static-writing/index.html 缺少 {asset} 的 ?v=YYYYMMDD-slug 版本号。")
    issues.extend(check_git_version_bump(versions))
    return issues


def check_git_version_bump(current_versions: dict[str, str]) -> list[str]:
    changed = _git_lines(["diff", "--name-only", "--", "app/static-writing/app.js", "app/static-writing/styles.css"])
    if not changed:
        return []
    old_index = _git_text(["show", "HEAD:app/static-writing/index.html"])
    if old_index is None:
        return []
    old_versions = {match.group("asset"): match.group("version") for match in VERSION_RE.finditer(old_index)}
    issues = []
    for changed_path in changed:
        asset = Path(changed_path).name
        if asset not in FRONTEND_ASSETS:
            continue
        if current_versions.get(asset) == old_versions.get(asset):
            issues.append(f"{changed_path} 已修改，但 index.html 中 {asset} 的版本号未更新。")
    return issues


def _should_skip(path: Path) -> bool:
    parts = set(path.relative_to(ROOT).parts)
    return bool(parts & SKIP_PARTS)


def _git_lines(args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def _git_text(args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


if __name__ == "__main__":
    sys.exit(main())
