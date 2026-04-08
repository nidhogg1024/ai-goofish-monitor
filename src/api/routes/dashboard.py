"""
Dashboard 概览路由
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_task_service
from src.services.dashboard_service import build_dashboard_snapshot
from src.services.task_service import TaskService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def get_dashboard_summary(
    task_service: TaskService = Depends(get_task_service),
):
    try:
        tasks = await task_service.get_all_tasks()
        return await build_dashboard_snapshot(tasks)
    except Exception as exc:
        logger.exception("加载 dashboard 数据失败: %s", exc)
        raise HTTPException(status_code=500, detail="加载 dashboard 数据失败，请稍后重试。")
