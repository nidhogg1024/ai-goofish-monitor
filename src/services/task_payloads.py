"""
任务接口响应序列化辅助。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

from src.domain.models.task import Task

if TYPE_CHECKING:
    from src.services.scheduler_service import SchedulerService
    from src.services.execution_queue_service import ExecutionQueueService


def serialize_timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_task(
    task: Task,
    scheduler_service: Optional[SchedulerService],
    execution_queue_service: Optional[ExecutionQueueService] = None,
) -> dict[str, Any]:
    payload = task.model_dump()
    next_run_time = None
    if task.id is not None and scheduler_service is not None:
        next_run_time = scheduler_service.get_next_run_time(task.id)
    payload["next_run_at"] = serialize_timestamp(next_run_time)
    is_queued = False
    execution_state = "idle"
    if task.id is not None and execution_queue_service is not None:
        is_queued = execution_queue_service.is_task_pending(task.id)
        is_active = execution_queue_service.is_task_active(task.id)
        if is_active or payload.get("is_running"):
            execution_state = "running"
        elif is_queued:
            execution_state = "queued"
    elif payload.get("is_running"):
        execution_state = "running"
    payload["is_queued"] = is_queued
    payload["execution_state"] = execution_state
    return payload


def serialize_tasks(
    tasks: list[Task],
    scheduler_service: Optional[SchedulerService],
    execution_queue_service: Optional[ExecutionQueueService] = None,
) -> list[dict[str, Any]]:
    return [serialize_task(task, scheduler_service, execution_queue_service) for task in tasks]
