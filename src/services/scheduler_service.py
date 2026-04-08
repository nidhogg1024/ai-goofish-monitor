"""
调度服务
负责管理定时任务的调度
"""
import logging
from datetime import datetime
from collections import defaultdict
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from typing import List

from src.core.cron_utils import build_cron_trigger, normalize_cron_expression
from src.domain.models.task import Task
from src.infrastructure.config.settings import settings as app_settings
from src.services.execution_queue_service import ExecutionQueueService

logger = logging.getLogger(__name__)

SCHEDULER_TIMEZONE = getattr(app_settings, "scheduler_timezone", "Asia/Shanghai")


class SchedulerService:
    """调度服务"""

    def __init__(self, execution_queue_service: ExecutionQueueService):
        self.scheduler = AsyncIOScheduler(
            timezone=SCHEDULER_TIMEZONE,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": app_settings.scheduler_misfire_grace_seconds,
            },
        )
        self.execution_queue_service = execution_queue_service
        self.jitter_seconds = app_settings.scheduler_jitter_seconds

    def start(self):
        """启动调度器"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("调度器已启动")

    def stop(self):
        """停止调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("调度器已停止")

    def get_next_run_time(self, task_id: int):
        job = self.scheduler.get_job(f"task_{task_id}")
        if job is None:
            return None

        next_run_time = getattr(job, "next_run_time", None)
        if next_run_time is not None:
            return next_run_time

        trigger = getattr(job, "trigger", None)
        if trigger is None or not hasattr(trigger, "get_next_fire_time"):
            return None

        try:
            now = datetime.now(self.scheduler.timezone)
            return trigger.get_next_fire_time(None, now)
        except Exception:
            return None

    async def reload_jobs(self, tasks: List[Task]):
        """重新加载所有定时任务"""
        logger.info("正在重新加载定时任务...")
        new_job_ids: set[str] = set()
        grouped_tasks: dict[str, list[Task]] = defaultdict(list)

        for task in tasks:
            if task.enabled and task.cron:
                grouped_tasks[normalize_cron_expression(task.cron) or task.cron].append(task)

        for normalized_cron, cron_tasks in grouped_tasks.items():
            for index, task in enumerate(sorted(cron_tasks, key=lambda item: (item.id or 0, item.task_name))):
                job_id = f"task_{task.id}"
                try:
                    trigger = self._build_staggered_trigger(
                        normalized_cron,
                        index=index,
                        group_size=len(cron_tasks),
                    )
                    self.scheduler.add_job(
                        self._run_task,
                        trigger=trigger,
                        args=[task.id, task.task_name],
                        id=job_id,
                        name=f"Scheduled: {task.task_name}",
                        replace_existing=True,
                        jitter=self.jitter_seconds,
                    )
                    new_job_ids.add(job_id)
                    logger.info(
                        "  -> 已为任务 '%s' 添加定时规则: '%s'"
                        " (错峰组 %d/%d)",
                        task.task_name, task.cron, index + 1, len(cron_tasks),
                    )
                except ValueError as e:
                    logger.warning("  -> [警告] 任务 '%s' 的 Cron 表达式无效: %s", task.task_name, e)

        for existing_job in self.scheduler.get_jobs():
            if existing_job.id not in new_job_ids:
                existing_job.remove()

        logger.info("定时任务加载完成")

    async def _run_task(self, task_id: int, task_name: str):
        """执行定时任务"""
        logger.info("定时任务触发: 任务 '%s' 进入执行队列...", task_name)
        await self.execution_queue_service.enqueue_task(task_id, task_name, source="scheduler")

    def _build_staggered_trigger(
        self,
        normalized_cron: str,
        *,
        index: int,
        group_size: int,
    ):
        parts = normalized_cron.split()
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts
            staggered_minute = self._stagger_minute_field(minute, index=index, group_size=group_size)
            expression = " ".join([staggered_minute, hour, day, month, day_of_week])
            return build_cron_trigger(expression, timezone=self.scheduler.timezone)

        if len(parts) == 6:
            second, minute, hour, day, month, day_of_week = parts
            staggered_minute = self._stagger_minute_field(minute, index=index, group_size=group_size)
            expression = " ".join([second, staggered_minute, hour, day, month, day_of_week])
            return build_cron_trigger(expression, timezone=self.scheduler.timezone)

        return build_cron_trigger(normalized_cron, timezone=self.scheduler.timezone)

    def _stagger_minute_field(self, minute_field: str, *, index: int, group_size: int) -> str:
        normalized_minute = str(minute_field).strip()
        if group_size <= 1:
            return normalized_minute

        step = self._extract_step(normalized_minute)
        if step is None or step <= 1:
            return normalized_minute

        offset = index % step
        return f"{offset}/{step}"

    def _extract_step(self, minute_field: str) -> int | None:
        """Extract the step interval from a cron minute field.

        Supports ``*/N`` and ``start/step`` forms. Range expressions like
        ``5-30/10`` are not currently handled and will return None.
        """
        text = minute_field.strip()
        if text.startswith("*/") and text[2:].isdigit():
            return int(text[2:])
        if "/" in text:
            start, step = text.split("/", 1)
            if start.isdigit() and step.isdigit():
                return int(step)
        return None
