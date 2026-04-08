"""
执行队列服务
统一管理任务入队、排队与固定 worker 执行。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from src.infrastructure.config.settings import settings as app_settings
from src.services.process_service import ProcessService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QueueTask:
    task_id: int
    task_name: str
    source: str = "scheduler"


class ExecutionQueueService:
    """将调度触发转换为固定 worker 池消费，避免瞬时拉起大量爬虫进程。"""

    def __init__(self, process_service: ProcessService, worker_count: int | None = None):
        self.process_service = process_service
        self.worker_count = max(1, worker_count or app_settings.scheduler_max_concurrent_tasks)
        self.queue: asyncio.Queue[QueueTask | None] = asyncio.Queue()
        self.worker_tasks: list[asyncio.Task] = []
        self.pending_task_ids: set[int] = set()
        self.active_task_ids: set[int] = set()
        self._started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            self._started = True
            self.worker_tasks = [
                asyncio.create_task(self._worker_loop(index), name=f"execution-queue-worker-{index + 1}")
                for index in range(self.worker_count)
            ]
            logger.info("执行队列已启动，worker 数量: %d", self.worker_count)

    async def stop(self) -> None:
        async with self._lock:
            if not self._started:
                return
            self._started = False
            for _ in self.worker_tasks:
                await self.queue.put(None)
            workers = list(self.worker_tasks)
            self.worker_tasks.clear()

        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

        self.pending_task_ids.clear()
        self.active_task_ids.clear()
        logger.info("执行队列已停止")

    async def enqueue_task(self, task_id: int, task_name: str, *, source: str = "scheduler") -> bool:
        async with self._lock:
            if self.process_service.is_running(task_id):
                logger.debug("执行队列: 任务 '%s' 已在运行中，跳过重复入队", task_name)
                return False
            if task_id in self.pending_task_ids or task_id in self.active_task_ids:
                logger.debug("执行队列: 任务 '%s' 已在队列中，跳过重复入队", task_name)
                return False

            self.pending_task_ids.add(task_id)
        await self.queue.put(QueueTask(task_id=task_id, task_name=task_name, source=source))
        logger.info("执行队列: 任务 '%s' 已入队，来源: %s，当前排队数: %d", task_name, source, self.queue_size)
        return True

    def cancel_task(self, task_id: int) -> bool:
        """从待执行集合中移除任务。

        NOTE: 由于 asyncio.Queue 不支持按值删除，已入队的 QueueTask 仍会
        被 worker 取出，但 worker 会在发现 task_id 不在 pending_task_ids 后
        立即跳过，因此实际不会执行。
        """
        if task_id in self.pending_task_ids:
            self.pending_task_ids.discard(task_id)
            logger.info("执行队列: 已取消任务 ID %d 的排队状态", task_id)
            return True
        return False

    @property
    def queue_size(self) -> int:
        """返回实际待执行的任务数（不含已取消但仍在 Queue 中的条目）。"""
        return len(self.pending_task_ids)

    def is_task_pending(self, task_id: int) -> bool:
        return task_id in self.pending_task_ids

    def is_task_active(self, task_id: int) -> bool:
        return task_id in self.active_task_ids

    def snapshot(self) -> dict[str, object]:
        return {
            "worker_count": self.worker_count,
            "queue_size": self.queue_size,
            "active_count": len(self.active_task_ids),
            "pending_task_ids": sorted(self.pending_task_ids),
            "active_task_ids": sorted(self.active_task_ids),
        }

    async def _worker_loop(self, worker_index: int) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                break

            if item.task_id not in self.pending_task_ids:
                self.queue.task_done()
                continue

            self.pending_task_ids.discard(item.task_id)
            self.active_task_ids.add(item.task_id)
            try:
                logger.info(
                    "执行队列 worker-%d: 开始执行任务 '%s' (来源: %s)",
                    worker_index + 1, item.task_name, item.source,
                )
                started = await self.process_service.start_task(item.task_id, item.task_name)
                if started:
                    await self.process_service.wait_for_task_exit(item.task_id)
            except Exception as exc:
                logger.error("执行队列 worker-%d: 执行任务 '%s' 失败: %s", worker_index + 1, item.task_name, exc)
            finally:
                self.active_task_ids.discard(item.task_id)
                self.queue.task_done()
