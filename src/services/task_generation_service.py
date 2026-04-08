"""
任务生成作业服务
"""
import asyncio
import logging
import time
from copy import deepcopy
from typing import Awaitable, Dict, Iterable, Optional
from uuid import uuid4

from src.domain.models.task import Task
from src.domain.models.task_generation import TaskGenerationJob, TaskGenerationStep

logger = logging.getLogger(__name__)

DEFAULT_GENERATION_STEPS: tuple[tuple[str, str], ...] = (
    ("prepare", "接收创建请求"),
    ("reference", "读取参考文件"),
    ("prompt", "构建提示词"),
    ("llm", "调用 AI 生成标准"),
    ("persist", "保存分析标准"),
    ("task", "创建任务记录"),
)

_TERMINAL_STATUSES = {"completed", "failed"}
_JOB_RETENTION_SECONDS = 600


class TaskGenerationService:
    """管理 AI 任务生成的后台作业状态"""

    def __init__(self, step_specs: Iterable[tuple[str, str]] = DEFAULT_GENERATION_STEPS):
        self._step_specs = tuple(step_specs)
        self._jobs: Dict[str, TaskGenerationJob] = {}
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

    async def create_job(self, task_name: str) -> TaskGenerationJob:
        job = TaskGenerationJob(
            job_id=uuid4().hex,
            task_name=task_name,
            steps=[
                TaskGenerationStep(key=key, label=label)
                for key, label in self._step_specs
            ],
        )
        async with self._lock:
            self._purge_stale_jobs()
            self._jobs[job.job_id] = job
            return deepcopy(job)

    async def get_job(self, job_id: str) -> Optional[TaskGenerationJob]:
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
                logger.error("后台任务生成作业异常: %s", t.exception())

        task.add_done_callback(_on_done)
        self._background_tasks.add(task)

    async def advance(self, job_id: str, step_key: str, message: str) -> TaskGenerationJob:
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

    async def complete(self, job_id: str, task: Task, message: str) -> TaskGenerationJob:
        async with self._lock:
            job = self._require_job(job_id)
            job.status = "completed"
            job.current_step = None
            job.message = message
            job.error = None
            job.task = task
            for step in job.steps:
                if step.status != "failed":
                    step.status = "completed"
            self._job_finished_at[job_id] = time.monotonic()
            return deepcopy(job)

    async def fail(
        self,
        job_id: str,
        error: str,
        step_key: Optional[str] = None,
    ) -> TaskGenerationJob:
        async with self._lock:
            job = self._require_job(job_id)
            failed_step = step_key or job.current_step
            job.status = "failed"
            job.error = error
            job.message = error
            job.current_step = failed_step
            if failed_step:
                step = self._find_step(job, failed_step)
                if step:
                    step.status = "failed"
                    step.message = error
            self._job_finished_at[job_id] = time.monotonic()
            return deepcopy(job)

    def _require_job(self, job_id: str) -> TaskGenerationJob:
        job = self._jobs.get(job_id)
        if not job:
            raise KeyError(f"任务生成作业不存在: {job_id}")
        return job

    def _find_step(self, job: TaskGenerationJob, step_key: str) -> Optional[TaskGenerationStep]:
        for step in job.steps:
            if step.key == step_key:
                return step
        return None

    def _find_step_index(self, job: TaskGenerationJob, step_key: str) -> int:
        for index, step in enumerate(job.steps):
            if step.key == step_key:
                return index
        raise KeyError(f"未知的任务生成步骤: {step_key}")
