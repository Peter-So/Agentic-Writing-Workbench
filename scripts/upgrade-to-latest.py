from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_REPO = "Peter-So/Agentic-Writing-Workbench"
MANIFEST_NAME = "upgrade-manifest.json"
BACKUP_ROOT = Path("backups") / "upgrades"
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}


@dataclass(frozen=True)
class UpgradePlan:
    source_root: Path
    target_root: Path
    manifest: dict[str, Any]
    files: list[tuple[Path, Path]]


def main() -> int:
    args = parse_args()
    target_root = args.project_dir.resolve()
    try:
        if args.rollback:
            rollback(target_root, args.rollback.resolve(), dry_run=args.dry_run)
            return 0
        assert_project_root(target_root)
        with source_context(args) as source_root:
            plan = build_plan(source_root, target_root)
            if args.dry_run:
                print_plan(plan, dry_run=True)
                return 0
            backup_dir = apply_upgrade(plan)
            print(f"升级完成。备份目录：{backup_dir}")
            print("如需回滚：")
            print(f"  {sys.executable} scripts\\upgrade-to-latest.py --rollback \"{backup_dir}\"")
            return 0
    except UpgradeError as exc:
        print(f"升级失败：{exc}", file=sys.stderr)
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upgrade Agentic Writing Workbench from the latest GitHub release while preserving local user data."
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Agentic-Writing-Workbench project directory. Defaults to the current script's project root.",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"GitHub repository in owner/name format. Defaults to {DEFAULT_REPO}.",
    )
    parser.add_argument(
        "--version",
        help="Release tag to download. Defaults to the latest release.",
    )
    parser.add_argument(
        "--archive-url",
        help="Download a release zip from this URL instead of querying GitHub.",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="Use an already extracted release directory. Useful for tests and offline upgrades.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned changes without copying, backing up, or rolling back files.",
    )
    parser.add_argument(
        "--rollback",
        type=Path,
        help="Rollback a previous upgrade using a backup directory under backups/upgrades.",
    )
    return parser.parse_args()


class UpgradeError(RuntimeError):
    pass


class source_context:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.temp_dir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        if self.args.source_dir:
            source_root = self.args.source_dir.resolve()
            assert_project_root(source_root, source=True)
            return source_root

        self.temp_dir = tempfile.TemporaryDirectory(prefix="aww-upgrade-")
        archive_path = Path(self.temp_dir.name) / "release.zip"
        url = self.args.archive_url or resolve_release_zip_url(self.args.repo, self.args.version)
        print(f"下载发布包：{url}")
        download(url, archive_path)
        extract_dir = Path(self.temp_dir.name) / "release"
        extract_zip(archive_path, extract_dir)
        source_root = find_release_root(extract_dir)
        assert_project_root(source_root, source=True)
        return source_root

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.temp_dir:
            self.temp_dir.cleanup()


def resolve_release_zip_url(repo: str, version: str | None) -> str:
    if version:
        return f"https://github.com/{repo}/archive/refs/tags/{version}.zip"
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise UpgradeError(f"无法读取 GitHub latest release：{exc}") from exc
    assets = data.get("assets") or []
    zip_assets = [
        asset.get("browser_download_url")
        for asset in assets
        if isinstance(asset, dict)
        and str(asset.get("name", "")).lower().endswith(".zip")
        and asset.get("browser_download_url")
    ]
    return zip_assets[0] if zip_assets else str(data.get("zipball_url") or "")


def download(url: str, target: Path) -> None:
    if not url:
        raise UpgradeError("没有可用的发布包下载地址。")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            with target.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except OSError as exc:
        raise UpgradeError(f"下载发布包失败：{exc}") from exc


def extract_zip(archive_path: Path, extract_dir: Path) -> None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise UpgradeError("发布包不是有效 zip 文件。") from exc


def find_release_root(extract_dir: Path) -> Path:
    candidates = [extract_dir, *[path for path in extract_dir.iterdir() if path.is_dir()]]
    for candidate in candidates:
        if (candidate / "README.md").exists() and (candidate / "app").is_dir():
            return candidate
    raise UpgradeError("发布包中没有找到 Agentic Writing Workbench 项目根目录。")


def assert_project_root(root: Path, source: bool = False) -> None:
    required = ["README.md", "app", "scripts"]
    missing = [name for name in required if not (root / name).exists()]
    if missing:
        label = "升级源" if source else "目标项目"
        raise UpgradeError(f"{label}不是有效项目根目录，缺少：{', '.join(missing)}")


def build_plan(source_root: Path, target_root: Path) -> UpgradePlan:
    manifest = load_manifest(source_root, target_root)
    replace_entries = normalize_entries(manifest.get("replace", []))
    preserve_entries = normalize_entries(manifest.get("preserve", []))
    if not replace_entries:
        raise UpgradeError(f"{MANIFEST_NAME} 缺少 replace 清单。")

    files: list[tuple[Path, Path]] = []
    for entry in replace_entries:
        source = source_root / entry
        if not source.exists():
            continue
        if source.is_file():
            relative = Path(entry)
            if not is_preserved(relative, preserve_entries):
                files.append((source, target_root / relative))
            continue
        for path in source.rglob("*"):
            if not path.is_file() or should_skip(path):
                continue
            relative = path.relative_to(source_root)
            if is_preserved(relative, preserve_entries):
                continue
            files.append((path, target_root / relative))

    files = sorted(files, key=lambda pair: pair[1].as_posix().lower())
    if not files:
        raise UpgradeError("没有可升级的框架文件。")
    return UpgradePlan(source_root=source_root, target_root=target_root, manifest=manifest, files=files)


def load_manifest(source_root: Path, target_root: Path) -> dict[str, Any]:
    source_manifest = source_root / MANIFEST_NAME
    target_manifest = target_root / MANIFEST_NAME
    manifest_path = source_manifest if source_manifest.exists() else target_manifest
    if not manifest_path.exists():
        raise UpgradeError(f"缺少 {MANIFEST_NAME}。")
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UpgradeError(f"{manifest_path} 不是有效 JSON：{exc}") from exc


def normalize_entries(entries: object) -> list[str]:
    if not isinstance(entries, list):
        return []
    normalized = []
    for entry in entries:
        value = str(entry).replace("\\", "/").strip("/")
        if value:
            normalized.append(value)
    return normalized


def is_preserved(relative: Path, preserve_entries: list[str]) -> bool:
    rel = relative.as_posix().strip("/")
    for entry in preserve_entries:
        prefix = entry.rstrip("/")
        if entry.endswith("/"):
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
            continue
        if rel == prefix or fnmatch.fnmatch(rel, prefix):
            return True
    return False


def should_skip(path: Path) -> bool:
    return bool(set(path.parts) & SKIP_DIRS)


def apply_upgrade(plan: UpgradePlan) -> Path:
    backup_dir = make_backup_dir(plan.target_root)
    backup_files_dir = backup_dir / "files"
    records: list[dict[str, Any]] = []
    print_plan(plan, dry_run=False)

    try:
        for source, target in plan.files:
            relative = target.relative_to(plan.target_root)
            record = {
                "path": relative.as_posix(),
                "existed": target.exists(),
                "sha256_before": sha256(target) if target.exists() else None,
                "sha256_source": sha256(source),
            }
            if target.exists():
                backup_target = backup_files_dir / relative
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_target)
            records.append(record)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            record["sha256_after"] = sha256(target)
    except Exception as exc:
        write_backup_manifest(backup_dir, plan, records)
        try:
            rollback(plan.target_root, backup_dir)
        except Exception as rollback_exc:
            raise UpgradeError(
                f"升级复制失败，且自动回滚失败：{type(exc).__name__}: {exc}; "
                f"rollback={type(rollback_exc).__name__}: {rollback_exc}"
            ) from rollback_exc
        raise UpgradeError(f"升级复制失败，已自动回滚：{type(exc).__name__}: {exc}") from exc
    write_backup_manifest(backup_dir, plan, records)
    return backup_dir


def make_backup_dir(target_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = target_root / BACKUP_ROOT / stamp
    counter = 1
    while backup_dir.exists():
        backup_dir = target_root / BACKUP_ROOT / f"{stamp}-{counter}"
        counter += 1
    backup_dir.mkdir(parents=True, exist_ok=False)
    return backup_dir


def write_backup_manifest(backup_dir: Path, plan: UpgradePlan, records: list[dict[str, Any]]) -> None:
    payload = {
        "schema": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "target_root": str(plan.target_root),
        "source_root": str(plan.source_root),
        "files": records,
        "preserve": plan.manifest.get("preserve", []),
        "replace": plan.manifest.get("replace", []),
    }
    (backup_dir / "backup-manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def rollback(target_root: Path, backup_dir: Path, dry_run: bool = False) -> None:
    manifest_path = backup_dir / "backup-manifest.json"
    if not manifest_path.exists():
        raise UpgradeError(f"回滚目录缺少 backup-manifest.json：{backup_dir}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = data.get("files")
    if not isinstance(files, list):
        raise UpgradeError("备份清单缺少 files。")
    print(f"{'计划回滚' if dry_run else '开始回滚'}：{backup_dir}")
    for record in reversed(files):
        relative = Path(str(record.get("path", "")))
        if not relative.as_posix() or relative.is_absolute() or ".." in relative.parts:
            raise UpgradeError(f"备份清单包含非法路径：{relative}")
        target = target_root / relative
        backup_file = backup_dir / "files" / relative
        existed = bool(record.get("existed"))
        if dry_run:
            action = "还原" if existed else "删除升级新增文件"
            print(f"- {action}: {relative.as_posix()}")
            continue
        if existed:
            if not backup_file.exists():
                raise UpgradeError(f"备份文件缺失，无法回滚：{backup_file}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, target)
        elif target.exists():
            target.unlink()
            cleanup_empty_parents(target.parent, target_root)
    print("回滚完成。")


def cleanup_empty_parents(path: Path, root: Path) -> None:
    while path != root and path.exists():
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent


def print_plan(plan: UpgradePlan, dry_run: bool) -> None:
    print(f"{'升级预演' if dry_run else '开始升级'}：")
    print(f"- 升级源：{plan.source_root}")
    print(f"- 目标项目：{plan.target_root}")
    print(f"- 框架文件：{len(plan.files)} 个")
    for _source, target in plan.files[:30]:
        marker = "更新" if target.exists() else "新增"
        print(f"  {marker} {target.relative_to(plan.target_root).as_posix()}")
    if len(plan.files) > 30:
        print(f"  ... 还有 {len(plan.files) - 30} 个文件")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    sys.exit(main())
