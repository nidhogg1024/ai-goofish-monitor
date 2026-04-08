"""
批量任务生成作业模型
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.domain.models.task_generation import TaskGenerationStep


class BatchGenerationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BatchPreview(BaseModel):
    """AI 返回的单个批量任务预览。"""
    task_name: str = "未命名任务"
    keyword: str = ""
    reason: str = ""
    description: str = ""
    min_price: Optional[str] = None
    max_price: Optional[str] = None
    personal_only: bool = True
    free_shipping: bool = True
    region: str = ""
    analyze_images: bool = True
    decision_mode: str = "ai"


class BatchGenerationJob(BaseModel):
    """批量任务生成作业"""

    model_config = ConfigDict(use_enum_values=True)

    job_id: str
    status: BatchGenerationStatus = BatchGenerationStatus.QUEUED
    message: str = "批量解析已排队，等待开始。"
    current_step: Optional[str] = None
    steps: List[TaskGenerationStep] = Field(default_factory=list)
    previews: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
