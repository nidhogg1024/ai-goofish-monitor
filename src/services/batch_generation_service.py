"""
批量任务生成作业状态管理

NOTE: 此服务与 TaskGenerationService 高度相似，后续应考虑提取
AbstractGenerationService 基类以消除重复代码。
"""
import asyncio
import logging
import time
from copy import deepcopy
from typing import Any, Awaitable, Dict, List, Optional
from uuid import uuid4

from src.domain.models.batch_generation import BatchGenerationJob
from src.domain.models.task_generation import TaskGenerationStep

logger = logging.getLogger(__name__)

BATCH_GENERATION_STEPS: tuple[tuple[str, str], ...] = (
    ("fetch", "获取输入内容"),
    ("analyze", "AI 深度分析"),
    ("parse", "解析任务配置"),
)

_TERMINAL_STATUSES = {"completed", "failed"}
_JOB_RETENTION_SECONDS = 600


class BatchGenerationService:
    """管理批量任务生成的后台作业状态"""

    def __init__(self) -> None:
        self._jobs: Dict[str, BatchGenerationJob] = {}
        self._job_finished_at: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task] = set()

    def _purge_stale_jobs(self) -> None:
        now = time.monotonic()
        stale = [
            jid for jid, finished_at in self._job_finished_at.items()
            if now - finished_at > _JOB_RETENTION_SECONDS
        ]
        for jid in stale:
            self._jobs.pop(jid, None)
            self._job_finished_at.pop(jid, None)

    async def create_job(self) -> BatchGenerationJob:
        job = BatchGenerationJob(
            job_id=uuid4().hex,
            steps=[
                TaskGenerationStep(key=key, label=label)
                for key, label in BATCH_GENERATION_STEPS
            ],
        )
        async with self._lock:
            self._purge_stale_jobs()
            self._jobs[job.job_id] = job
            return deepcopy(job)

    async def get_job(self, job_id: str) -> Optional[BatchGenerationJob]:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return deepcopy(job)

    def track(self, coroutine: Awaitable[None]) -> None:
        # TODO: Each call spawns a new thread + event loop. Ideally replace with
        # asyncio.create_task when we can guarantee a running event loop at call site.
        task = asyncio.ensure_future(coroutine)

        def _on_done(t: asyncio.Task) -> None:
            self._background_tasks.discard(t)
            if t.exception():
                logger.error("后台批量生成作业异常: %s", t.exception())

        task.add_done_callback(_on_done)
        self._background_tasks.add(task)

    async def advance(self, job_id: str, step_key: str, message: str) -> BatchGenerationJob:
        async with self._lock:
            job = self._require_job(job_id)
            target_index = self._find_step_index(job, step_key)
            job.status = "running"
            job.current_step = step_key
            job.message = message
            for index, step in enumerate(job.steps):
                if step.status == "failed":
                    continue
                if index < target_index:
                    step.status = "completed"
                elif index == target_index:
                    step.status = "running"
                    step.message = message
                elif step.status != "pending":
                    step.status = "pending"
                    step.message = ""
            return deepcopy(job)

    async def complete(
        self,
        job_id: str,
        previews: List[Dict[str, Any]],
        message: str,
    ) -> BatchGenerationJob:
        async with self._lock:
            job = self._require_job(job_id)
            job.status = "completed"
            job.current_step = None
            job.message = message
            job.error = None
            job.previews = previews
            for step in job.steps:
                if step.status != "failed":
                    step.status = "completed"
            self._job_finished_at[job_id] = time.monotonic()
            return deepcopy(job)

    async def fail(self, job_id: str, error: str) -> BatchGenerationJob:
        async with self._lock:
            job = self._require_job(job_id)
            failed_step = job.current_step
            job.status = "failed"
            job.error = error
            job.message = error
            if failed_step:
                step = self._find_step(job, failed_step)
                if step:
                    step.status = "failed"
                    step.message = error
            self._job_finished_at[job_id] = time.monotonic()
            return deepcopy(job)

    def _require_job(self, job_id: str) -> BatchGenerationJob:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"批量生成作业不存在: {job_id}")
        return job

    def _find_step(self, job: BatchGenerationJob, step_key: str) -> Optional[TaskGenerationStep]:
        for step in job.steps:
            if step.key == step_key:
                return step
        return None

    def _find_step_index(self, job: BatchGenerationJob, step_key: str) -> int:
        for index, step in enumerate(job.steps):
            if step.key == step_key:
                return index
        raise KeyError(f"未知的批量生成步骤: {step_key}")
