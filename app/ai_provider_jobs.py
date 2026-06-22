from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from time import perf_counter
from typing import Any


class ProviderJob:
    """一次 AI 协同执行的状态容器：记录每个 provider 的运行/完成状态与计时。"""

    def __init__(self, job_id: str, provider_ids: list[str], prompt: str, format_for_writing: bool) -> None:
        self.job_id = job_id
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.prompt = prompt
        self.prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        self.format_for_writing = format_for_writing
        self.done = False
        self.result: dict[str, Any] | None = None
        self.providers: dict[str, dict[str, Any]] = {
            pid: {"provider": pid, "status": "queued", "elapsed_seconds": 0.0}
            for pid in provider_ids
        }
        self._t0: dict[str, float] = {}

    def mark_running(self, provider_id: str) -> None:
        self._t0[provider_id] = perf_counter()
        if provider_id in self.providers:
            self.providers[provider_id]["status"] = "running"

    def mark_done(self, provider_id: str, status: str, result: str | None = None,
                  name: str | None = None) -> None:
        elapsed = round(perf_counter() - self._t0.get(provider_id, perf_counter()), 1)
        if provider_id in self.providers:
            self.providers[provider_id]["status"] = status
            self.providers[provider_id]["elapsed_seconds"] = elapsed
            # 存每家结果，供前端"先完成先回显"按家增量渲染。
            if result is not None:
                self.providers[provider_id]["result"] = result
            if name is not None:
                self.providers[provider_id]["name"] = name

    def snapshot(self) -> dict[str, Any]:
        live: list[dict[str, Any]] = []
        for pid, info in self.providers.items():
            data = dict(info)
            if data["status"] == "running" and pid in self._t0:
                data["elapsed_seconds"] = round(perf_counter() - self._t0[pid], 1)
            live.append(data)
        return {
            "job_id": self.job_id,
            "done": self.done,
            "created_at": self.created_at,
            "prompt_hash": self.prompt_hash,
            "format_for_writing": self.format_for_writing,
            "providers": live,
            "result": self.result,
        }


class ProviderJobManager:
    def __init__(self, keep: int = 20) -> None:
        self._jobs: dict[str, ProviderJob] = {}
        self._keep = keep

    def create(self, provider_ids: list[str], prompt: str, format_for_writing: bool) -> ProviderJob:
        job = ProviderJob(uuid.uuid4().hex, provider_ids, prompt, format_for_writing)
        self._jobs[job.job_id] = job
        if len(self._jobs) > self._keep:
            for old in list(self._jobs)[: -self._keep]:
                self._jobs.pop(old, None)
        return job

    def get(self, job_id: str) -> ProviderJob | None:
        return self._jobs.get(job_id)


jobs = ProviderJobManager()
