from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_REPO = "Peter-So/Agentic-Writing-Workbench"
TAG_PREFIX = "Agentic-Writing-Workbench-v"


class ReleaseError(RuntimeError):
    pass


def main() -> int:
    args = parse_args()
    root = args.project_dir.resolve()
    try:
        assert_project_root(root)
        require_command("git")
        require_command("gh")
        version = normalize_version(args.version)
        tag = f"{TAG_PREFIX}{version}"
        notes = changelog_section(root / "CHANGE.md", version)
        if not notes:
            raise ReleaseError(
                f"版本不存在：CHANGE.md 中没有找到 '## {tag}'，请先补充更新日志再发布。"
            )
        ensure_clean_worktree(root)
        run(["git", "fetch", args.remote, "--tags"], cwd=root)
        ensure_not_published(root, args.repo, tag)
        if args.dry_run:
            print(f"dry-run: 将发布 {tag}")
            print(f"dry-run: 将推送 {args.remote} HEAD:{args.branch}")
            print("dry-run: Release notes:")
            print(notes)
            return 0
        publish(root, repo=args.repo, remote=args.remote, branch=args.branch, tag=tag, notes=notes)
        print(f"发布完成：{tag}")
        return 0
    except ReleaseError as exc:
        print(f"发布失败：{exc}", file=sys.stderr)
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Push main, create a version tag, and publish a GitHub Release for Agentic Writing Workbench."
    )
    parser.add_argument(
        "version",
        help="Version to publish, for example v0.1.5, 0.1.5, or Agentic-Writing-Workbench-v0.1.5.",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root. Defaults to the repository containing this script.",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"GitHub repo, defaults to {DEFAULT_REPO}.")
    parser.add_argument("--remote", default="origin", help="Git remote name, defaults to origin.")
    parser.add_argument("--branch", default="main", help="Remote branch to push HEAD to, defaults to main.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the release plan only.")
    return parser.parse_args()


def assert_project_root(root: Path) -> None:
    required = [root / ".git", root / "CHANGE.md", root / "app", root / "scripts"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise ReleaseError(f"不是有效项目根目录，缺少：{', '.join(missing)}")


def require_command(name: str) -> None:
    if not shutil.which(name):
        raise ReleaseError(f"缺少命令：{name}。请先安装并配置后再发布。")


def normalize_version(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith(TAG_PREFIX):
        text = text[len(TAG_PREFIX) :]
    if text.startswith("v"):
        text = text[1:]
    if not re.fullmatch(r"\d+\.\d+\.\d+", text):
        raise ReleaseError("版本格式错误，请使用 v0.1.5 或 0.1.5。")
    return text


def changelog_section(path: Path, version: str) -> str:
    text = path.read_text(encoding="utf-8")
    tag = f"{TAG_PREFIX}{version}"
    pattern = re.compile(rf"^##\s+{re.escape(tag)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.start()
    next_match = re.search(r"^##\s+", text[match.end() :], re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(text)
    return text[start:end].strip() + "\n"


def ensure_clean_worktree(root: Path) -> None:
    status = run(["git", "status", "--porcelain"], cwd=root, capture=True)
    if status.stdout.strip():
        raise ReleaseError("工作区存在未提交改动，请先提交或清理后再发布。")


def ensure_not_published(root: Path, repo: str, tag: str) -> None:
    local = run(["git", "tag", "--list", tag], cwd=root, capture=True)
    if local.stdout.strip():
        raise ReleaseError(f"本地 tag 已存在：{tag}")
    remote = run(["git", "ls-remote", "--tags", "origin", tag], cwd=root, capture=True)
    if remote.stdout.strip():
        raise ReleaseError(f"远端 tag 已存在：{tag}")
    release = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        cwd=root,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if release.returncode == 0:
        raise ReleaseError(f"GitHub Release 已存在：{tag}")


def publish(root: Path, *, repo: str, remote: str, branch: str, tag: str, notes: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(notes)
        notes_path = Path(fh.name)
    try:
        run(["git", "push", remote, f"HEAD:{branch}"], cwd=root)
        run(["git", "tag", "-a", tag, "-m", tag], cwd=root)
        run(["git", "push", remote, tag], cwd=root)
        run(["gh", "release", "create", tag, "--repo", repo, "--title", tag, "--notes-file", str(notes_path)], cwd=root)
    finally:
        try:
            notes_path.unlink(missing_ok=True)
        except OSError:
            pass


def run(cmd: list[str], *, cwd: Path, capture: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if proc.returncode != 0:
        detail = ""
        if capture:
            detail = (proc.stderr or proc.stdout or "").strip()
        raise ReleaseError(f"命令失败：{' '.join(cmd)}" + (f"\n{detail}" if detail else ""))
    return proc


if __name__ == "__main__":
    raise SystemExit(main())
