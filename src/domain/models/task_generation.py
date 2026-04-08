"""
任务生成作业模型
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.domain.models.task import Task


class TaskGenerationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskGenerationStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskGenerationStep(BaseModel):
    """单个任务生成步骤"""

    model_config = ConfigDict(use_enum_values=True)

    key: str
    label: str
    status: TaskGenerationStepStatus = TaskGenerationStepStatus.PENDING
    message: str = ""


class TaskGenerationJob(BaseModel):
    """任务生成作业"""

    model_config = ConfigDict(use_enum_values=True)

    job_id: str
    task_name: str
    status: TaskGenerationStatus = TaskGenerationStatus.QUEUED
    message: str = "任务已排队，等待开始。"
    current_step: Optional[str] = None
    steps: List[TaskGenerationStep] = Field(default_factory=list)
    task: Optional[Task] = None
    error: Optional[str] = None
