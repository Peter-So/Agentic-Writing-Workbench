from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    args = parse_args()
    wait_for_parent(args.parent_pid, args.timeout)
    wait_for_port_release(args.host, args.port, args.timeout)
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        args.app,
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    kwargs = {
        "cwd": str(args.project_dir.resolve()),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restart the Agentic Writing Workbench web server after upgrade.")
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--app", default="app.writing_web:app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def wait_for_parent(pid: int, timeout: float) -> None:
    if pid <= 0:
        time.sleep(1.0)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return
        time.sleep(0.4)


def process_exists(pid: int) -> bool:
    if sys.platform.startswith("win"):
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        import os

        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for_port_release(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex((host, port)) != 0:
                return
        time.sleep(0.4)


if __name__ == "__main__":
    sys.exit(main())
