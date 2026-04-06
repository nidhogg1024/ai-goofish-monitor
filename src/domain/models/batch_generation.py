"""
批量任务生成作业模型
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.domain.models.task_generation import TaskGenerationStep


BatchGenerationStatus = Literal["queued", "running", "completed", "failed"]


class BatchGenerationJob(BaseModel):
    """批量任务生成作业"""

    job_id: str
    status: BatchGenerationStatus = "queued"
    message: str = "批量解析已排队，等待开始。"
    current_step: Optional[str] = None
    steps: List[TaskGenerationStep] = Field(default_factory=list)
    previews: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
