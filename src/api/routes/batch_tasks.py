"""
批量任务生成路由
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List

from src.api.dependencies import (
    get_batch_generation_service,
    get_scheduler_service,
    get_task_service,
)
from src.domain.models.task import TaskGenerateRequest
from src.services.batch_generation_runner import run_batch_generation
from src.services.batch_generation_service import BatchGenerationService
from src.services.scheduler_service import SchedulerService
from src.services.task_generation_runner import build_criteria_filename, build_task_create
from src.services.task_intent_service import enrich_generate_request
from src.services.task_payloads import serialize_task
from src.services.task_service import TaskService

router = APIRouter(prefix="/api/tasks", tags=["batch-tasks"])


class BatchGenerateRequest(BaseModel):
    url: str | None = None
    description: str | None = None


class BatchCreateRequest(BaseModel):
    tasks: List[TaskGenerateRequest]


@router.post("/batch-generate", response_model=dict)
async def batch_generate(
    req: BatchGenerateRequest,
    service: BatchGenerationService = Depends(get_batch_generation_service),
):
    """提交批量任务解析请求"""
    url = (req.url or "").strip()
    description = (req.description or "").strip()

    if not url and not description:
        raise HTTPException(status_code=400, detail="请至少填写链接或需求描述。")

    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请输入有效的 URL 地址。")

    job = await service.create_job()
    service.track(
        run_batch_generation(
            job_id=job.job_id,
            url=url or None,
            description=description or None,
            service=service,
        )
    )
    return JSONResponse(
        status_code=202,
        content={
            "message": "批量解析已开始。",
            "job": job.model_dump(mode="json"),
        },
    )


@router.get("/batch-generate-jobs/{job_id}", response_model=dict)
async def get_batch_generation_job(
    job_id: str,
    service: BatchGenerationService = Depends(get_batch_generation_service),
):
    """获取批量任务生成作业状态"""
    job = await service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="批量生成作业未找到")
    return {"job": job.model_dump(mode="json")}


@router.post("/batch-create", response_model=dict)
async def batch_create(
    req: BatchCreateRequest,
    task_service: TaskService = Depends(get_task_service),
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
):
    """批量创建任务（用户确认预览后调用）

    跳过 criteria 文件生成，任务秒创建。criteria 会在任务首次运行时自动生成。
    """
    if not req.tasks:
        raise HTTPException(status_code=400, detail="任务列表不能为空。")

    results = []
    for task_req in req.tasks:
        try:
            task_req = await enrich_generate_request(task_req)
            mode = task_req.decision_mode or "ai"
            criteria_file = build_criteria_filename(task_req.keyword) if mode == "ai" else ""
            task = await task_service.create_task(build_task_create(task_req, criteria_file))
            results.append({
                "success": True,
                "task": serialize_task(task, scheduler_service),
            })
        except Exception as exc:
            results.append({
                "success": False,
                "task_name": task_req.task_name or task_req.keyword or "未知",
                "error": str(exc),
            })

    # Reload scheduler
    try:
        all_tasks = await task_service.get_all_tasks()
        await scheduler_service.reload_jobs(all_tasks)
    except Exception:
        pass

    success_count = sum(1 for r in results if r["success"])
    return {
        "message": f"批量创建完成：{success_count}/{len(results)} 个任务创建成功。",
        "results": results,
    }
