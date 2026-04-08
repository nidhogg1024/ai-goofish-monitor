"""
任务生成作业执行器
"""
import logging
import os

import aiofiles

logger = logging.getLogger(__name__)

from src.domain.models.task import TaskCreate, TaskGenerateRequest
from src.prompt_utils import generate_criteria
from src.services.scheduler_service import SchedulerService
from src.services.task_schedule_service import resolve_request_cron
from src.services.task_generation_service import TaskGenerationService
from src.services.task_service import TaskService
from src.services.task_taxonomy_service import ensure_task_taxonomy


REFERENCE_FILE_PATH = "prompts/macbook_criteria.txt"


def build_criteria_filename(keyword: str) -> str:
    if not keyword or not keyword.strip():
        raise ValueError("keyword 不能为空，无法生成 criteria 文件名")
    safe_keyword = "".join(
        char for char in keyword.lower().replace(" ", "_")
        if char.isalnum() or char in "_-"
    ).rstrip()
    return f"prompts/{safe_keyword}_criteria.txt"


def build_task_create(req: TaskGenerateRequest, criteria_file: str, *, cron: str | None = None) -> TaskCreate:
    task_name = (req.task_name or req.keyword or "未命名任务").strip()
    keyword = (req.keyword or req.task_name or "").strip()
    category, group_name = ensure_task_taxonomy(
        category=req.category,
        group_name=req.group_name,
        task_name=task_name,
        keyword=keyword,
        description=req.description,
    )
    return TaskCreate(
        task_name=task_name,
        category=category,
        group_name=group_name,
        enabled=True,
        keyword=keyword,
        description=req.description or "",
        analyze_images=req.analyze_images,
        max_pages=req.max_pages,
        first_scan_max_pages=req.first_scan_max_pages,
        personal_only=req.personal_only,
        min_price=req.min_price,
        max_price=req.max_price,
        cron=cron,
        ai_prompt_base_file="prompts/base_prompt.txt",
        ai_prompt_criteria_file=criteria_file,
        account_state_file=req.account_state_file,
        account_strategy=req.account_strategy,
        free_shipping=req.free_shipping,
        new_publish_option=req.new_publish_option,
        region=req.region,
        decision_mode=req.decision_mode or "ai",
        keyword_rules=req.keyword_rules,
    )


async def save_generated_criteria(output_filename: str, generated_criteria: str) -> None:
    if not generated_criteria or not generated_criteria.strip():
        raise RuntimeError("AI 未能生成分析标准，返回内容为空。")

    os.makedirs("prompts", exist_ok=True)
    async with aiofiles.open(output_filename, "w", encoding="utf-8") as file:
        await file.write(generated_criteria)


async def reload_scheduler(
    task_service: TaskService,
    scheduler_service: SchedulerService,
) -> None:
    tasks = await task_service.get_all_tasks()
    await scheduler_service.reload_jobs(tasks)


async def advance_job(
    generation_service: TaskGenerationService,
    job_id: str,
    step_key: str,
    message: str,
) -> None:
    await generation_service.advance(job_id, step_key, message)


async def run_ai_generation_job(
    *,
    job_id: str,
    req: TaskGenerateRequest,
    task_service: TaskService,
    scheduler_service: SchedulerService,
    generation_service: TaskGenerationService,
) -> None:
    # TODO: 目前没有超时控制，长时间挂起的 AI 调用无法中断
    output_filename = build_criteria_filename(req.keyword)
    file_existed_before = os.path.exists(output_filename)
    try:
        await advance_job(
            generation_service,
            job_id,
            "prepare",
            "已接收请求，开始准备分析标准。",
        )

        async def report_progress(step_key: str, message: str) -> None:
            await advance_job(generation_service, job_id, step_key, message)

        generated_criteria = await generate_criteria(
            user_description=req.description or "",
            reference_file_path=REFERENCE_FILE_PATH,
            progress_callback=report_progress,
        )

        await advance_job(
            generation_service,
            job_id,
            "persist",
            f"正在保存分析标准到 {output_filename}。",
        )
        await save_generated_criteria(output_filename, generated_criteria)

        await advance_job(
            generation_service,
            job_id,
            "task",
            "分析标准已生成，正在创建任务记录。",
        )
        all_tasks = await task_service.get_all_tasks()
        resolved_cron = resolve_request_cron(req, existing_tasks=all_tasks)
        task = await task_service.create_task(build_task_create(req, output_filename, cron=resolved_cron))
        await reload_scheduler(task_service, scheduler_service)
        await generation_service.complete(job_id, task, f"任务“{req.task_name}”创建完成。")
    except Exception as exc:
        if not file_existed_before and os.path.exists(output_filename):
            os.remove(output_filename)
        logger.error("AI 任务生成失败: %s", exc, exc_info=True)
        await generation_service.fail(job_id, f"AI 任务生成失败: {exc}")
