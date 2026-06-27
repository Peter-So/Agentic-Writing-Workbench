from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPGRADE_SCRIPT = ROOT / "scripts" / "upgrade-to-latest.py"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aww-upgrade-test-") as temp:
        base = Path(temp)
        target = base / "target"
        source = base / "source"
        create_target(target)
        create_source(source)

        run("--project-dir", str(target), "--source-dir", str(source), "--dry-run")
        assert_text(target / "app" / "static-writing" / "app.js", "old app")
        assert_no_backups(target)

        run("--project-dir", str(target), "--source-dir", str(source))
        assert_text(target / "app" / "static-writing" / "app.js", "new app")
        assert_text(target / "scripts" / "framework-tool.py", "new tool")
        assert_text(target / "docs" / "upgrade.md", "new doc")
        assert_text(target / ".env.shared", "SECRET=local")
        assert_text(target / "projects" / "writing" / "novels" / "001" / "正文" / "chapter.md", "user chapter")
        assert_text(target / "data" / "knowledge.md", "user knowledge")

        backup_dir = latest_backup(target)
        manifest = json.loads((backup_dir / "backup-manifest.json").read_text(encoding="utf-8"))
        assert manifest["files"], "backup manifest should record changed files"

        run("--project-dir", str(target), "--rollback", str(backup_dir))
        assert_text(target / "app" / "static-writing" / "app.js", "old app")
        assert_text(target / "scripts" / "framework-tool.py", "old tool")
        assert not (target / "docs" / "upgrade.md").exists(), "rollback should remove files created by upgrade"
        assert_text(target / ".env.shared", "SECRET=local")
        assert_text(target / "projects" / "writing" / "novels" / "001" / "正文" / "chapter.md", "user chapter")
        assert_text(target / "data" / "knowledge.md", "user knowledge")

    print("upgrade workflow test passed")
    return 0


def create_target(root: Path) -> None:
    write(root / "README.md", "target readme")
    write(root / "app" / "static-writing" / "app.js", "old app")
    write(root / "scripts" / "framework-tool.py", "old tool")
    write(root / ".env.shared", "SECRET=local")
    write(root / "projects" / "writing" / "novels" / "001" / "正文" / "chapter.md", "user chapter")
    write(root / "data" / "knowledge.md", "user knowledge")
    write(root / "upgrade-manifest.json", manifest_text())


def create_source(root: Path) -> None:
    write(root / "README.md", "source readme")
    write(root / "app" / "static-writing" / "app.js", "new app")
    write(root / "scripts" / "framework-tool.py", "new tool")
    write(root / "docs" / "upgrade.md", "new doc")
    write(root / "projects" / "writing" / "novels" / "001" / "正文" / "chapter.md", "SHOULD NOT COPY")
    write(root / "data" / "knowledge.md", "SHOULD NOT COPY")
    write(root / ".env.shared", "SHOULD NOT COPY")
    write(root / "upgrade-manifest.json", manifest_text())


def manifest_text() -> str:
    return json.dumps(
        {
            "schema": 1,
            "replace": ["app/", "docs/", "scripts/", "README.md", "upgrade-manifest.json"],
            "preserve": [".env.shared", "backups/", "data/", "projects/"],
        },
        ensure_ascii=False,
        indent=2,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, str(UPGRADE_SCRIPT), *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout)


def assert_text(path: Path, expected: str) -> None:
    actual = path.read_text(encoding="utf-8")
    assert actual == expected, f"{path} expected {expected!r}, got {actual!r}"


def assert_no_backups(target: Path) -> None:
    backup_root = target / "backups" / "upgrades"
    assert not backup_root.exists(), "dry-run should not create backups"


def latest_backup(target: Path) -> Path:
    backup_root = target / "backups" / "upgrades"
    backups = sorted(path for path in backup_root.iterdir() if path.is_dir())
    assert backups, "upgrade should create backup"
    return backups[-1]


if __name__ == "__main__":
    sys.exit(main())
