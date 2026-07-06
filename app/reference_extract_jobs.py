from __future__ import annotations

import threading
import uuid
from datetime import datetime
from time import perf_counter
from typing import Any

from app.novel_context import normalize_novel_id
from app.reference_importer import extract_reference_full_book, list_imported_reference_novels


class ReferenceExtractJob:
    def __init__(self, *, title: str, novel_id: str, estimated_seconds: int) -> None:
        self.job_id = uuid.uuid4().hex
        self.title = title
        self.novel_id = normalize_novel_id(novel_id)
        self.estimated_seconds = estimated_seconds
        self.created_at = _now()
        self.updated_at = self.created_at
        self.status = "queued"
        self.stage = "queued"
        self.label = "等待整本抽取"
        self.details: dict[str, Any] = {}
        self.result: dict[str, Any] | None = None
        self.error = ""
        self.events: list[dict[str, Any]] = []
        self._started = perf_counter()
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def mark(self, *, status: str, stage: str, label: str, details: dict[str, Any] | None = None) -> None:
        with self._lock:
            self.status = status
            self.stage = stage
            self.label = label
            self.details = details or {}
            self.updated_at = _now()
            self.events.append({
                "at": self.updated_at,
                "status": status,
                "stage": stage,
                "label": label,
                "details": self.details,
            })
            self.events = self.events[-30:]

    def set_result(self, result: dict[str, Any]) -> None:
        status = "skipped" if not result.get("changed") else "completed"
        label = result.get("message") or ("已跳过重复抽取" if status == "skipped" else "整本抽取完成")
        with self._lock:
            self.status = status
            self.stage = "done"
            self.label = label
            self.result = result
            self.updated_at = _now()
            self.events.append({"at": self.updated_at, "status": status, "stage": "done", "label": label, "details": {}})
        _record_job_event(self, status=status, label=label, details={
            "title": result.get("title") or self.title,
            "total_dimension_matches": result.get("total_dimension_matches")
            or (result.get("extraction") or {}).get("total_dimension_matches")
            or 0,
            "dimension_coverage": result.get("dimension_coverage")
            or (result.get("extraction") or {}).get("dimension_coverage")
            or {},
        })

    def set_error(self, exc: BaseException) -> None:
        with self._lock:
            self.status = "failed"
            self.stage = "failed"
            self.label = "整本抽取失败"
            self.error = f"{type(exc).__name__}: {exc}"
            self.updated_at = _now()
            self.events.append({
                "at": self.updated_at,
                "status": "failed",
                "stage": "failed",
                "label": self.label,
                "details": {"error": self.error},
            })
        _record_job_event(self, status="failed", label=self.label, details={"error": self.error})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            status = self.status
            interrupted = False
            if status in {"queued", "running"} and self.thread is not None and not self.thread.is_alive():
                status = "interrupted"
                interrupted = True
            elapsed = round(perf_counter() - self._started, 1)
            return {
                "ok": True,
                "job_id": self.job_id,
                "title": self.title,
                "novel_id": self.novel_id,
                "status": status,
                "interrupted": interrupted,
                "stage": self.stage,
                "label": "后台任务已中断，请重新启动整本抽取。" if interrupted else self.label,
                "estimated_seconds": self.estimated_seconds,
                "elapsed_seconds": elapsed,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "details": self.details,
                "result": self.result,
                "error": self.error if not interrupted else "worker_thread_stopped",
                "events": list(self.events),
            }


class ReferenceExtractJobManager:
    def __init__(self, keep: int = 20) -> None:
        self._jobs: dict[str, ReferenceExtractJob] = {}
        self._keep = keep
        self._lock = threading.Lock()

    def start(self, *, title: str, novel_id: str) -> ReferenceExtractJob:
        with self._lock:
            for job in reversed(list(self._jobs.values())):
                snap = job.snapshot()
                if snap.get("title") == title and snap.get("status") in {"queued", "running"}:
                    return job
            estimated = _estimate_for_title(title)
            job = ReferenceExtractJob(title=title, novel_id=novel_id, estimated_seconds=estimated)
            self._jobs[job.job_id] = job
            _record_job_event(job, status="queued", label="参考小说整本抽取排队", details={"estimated_seconds": estimated})
            if len(self._jobs) > self._keep:
                for old in list(self._jobs)[: -self._keep]:
                    self._jobs.pop(old, None)

        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        job.thread = thread
        thread.start()
        return job

    def get(self, job_id: str) -> ReferenceExtractJob | None:
        return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        return [job.snapshot() for job in reversed(list(self._jobs.values()))]

    def _run(self, job: ReferenceExtractJob) -> None:
        def progress(stage: str, label: str, status: str = "running", details: dict[str, Any] | None = None) -> None:
            mapped = "running" if status in {"running", "done"} else status
            job.mark(status=mapped, stage=stage, label=label, details=details)
            _record_job_event(job, status=mapped, label=label, details={"stage": stage, **(details or {})})

        job.mark(status="running", stage="start", label="开始整本抽取")
        _record_job_event(job, status="running", label="开始参考小说整本抽取")
        try:
            result = extract_reference_full_book(title=job.title, progress=progress)
            job.set_result(result)
        except BaseException as exc:
            job.set_error(exc)


def _estimate_for_title(title: str) -> int:
    try:
        data = list_imported_reference_novels()
        for item in data.get("novels") or []:
            if item.get("title") == title:
                return int(item.get("estimated_seconds") or 30)
    except Exception:
        pass
    return 30


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _record_job_event(job: ReferenceExtractJob, *, status: str, label: str, details: dict[str, Any] | None = None) -> None:
    try:
        from app.writing_task_center import record_task_event

        record_task_event(
            task_type="reference_extract",
            task_id=job.job_id,
            status=status,
            label=f"{label}：{job.title}",
            novel_id=job.novel_id,
            source="reference_extract_jobs",
            details={
                "title": job.title,
                "estimated_seconds": job.estimated_seconds,
                **(details or {}),
            },
        )
    except Exception:
        pass


reference_extract_jobs = ReferenceExtractJobManager()
