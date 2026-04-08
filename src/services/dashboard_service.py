"""
Dashboard 聚合服务
统一汇总任务、结果文件和最近活动，供首页概览使用。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.domain.models.task import Task
from src.services.dashboard_payloads import (
    build_empty_summary,
    build_task_state_activities,
    normalize_text,
    serialize_timestamp,
    sort_key_by_activity_time,
    sort_key_by_latest_time,
    summarize_result_file,
)
from src.services.result_storage_service import list_result_filenames

logger = logging.getLogger(__name__)

MAX_RECENT_ACTIVITIES = 8


def _build_summary_metrics(tasks: list[Task], summary_list: list[dict[str, Any]], last_updated_at: Any) -> dict[str, Any]:
    return {
        "enabled_tasks": sum(1 for task in tasks if task.enabled),
        "running_tasks": sum(1 for task in tasks if task.is_running),
        "result_files": sum(1 for item in summary_list if item.get("filename")),
        "scanned_items": sum(item["total_items"] for item in summary_list),
        "recommended_items": sum(item["recommended_items"] for item in summary_list),
        "ai_recommended_items": sum(item["ai_recommended_items"] for item in summary_list),
        "keyword_recommended_items": sum(item["keyword_recommended_items"] for item in summary_list),
        "last_updated_at": serialize_timestamp(last_updated_at),
    }


async def build_dashboard_snapshot(tasks: list[Task]) -> dict[str, Any]:
    # Multiple tasks with the same keyword: use list to avoid overwrite
    task_lookup: dict[str, list[Task]] = {}
    for task in tasks:
        key = normalize_text(task.keyword)
        task_lookup.setdefault(key, []).append(task)
    # Flatten to single-task lookup for backward compat (last wins)
    flat_lookup = {k: v[-1] for k, v in task_lookup.items()}
    task_summaries: dict[str, dict[str, Any]] = {
        task.task_name: build_empty_summary(task) for task in tasks
    }
    recent_activities = build_task_state_activities(tasks)
    latest_updated_at = None

    filenames = await list_result_filenames()
    summaries_coros = [summarize_result_file(f, flat_lookup) for f in filenames]
    results = await asyncio.gather(*summaries_coros, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logger.error("Dashboard 汇总文件异常: %s", result)
            continue
        summary, activities, file_latest_time = result
        if summary:
            task_summaries[summary["task_name"]] = summary
        recent_activities.extend(activities)
        if file_latest_time and (latest_updated_at is None or file_latest_time > latest_updated_at):
            latest_updated_at = file_latest_time

    summary_list = sorted(task_summaries.values(), key=sort_key_by_latest_time, reverse=True)
    focus_file = next((item["filename"] for item in summary_list if item.get("filename")), None)
    return {
        "summary": _build_summary_metrics(tasks, summary_list, latest_updated_at),
        "task_summaries": summary_list,
        "recent_activities": sorted(
            recent_activities,
            key=sort_key_by_activity_time,
            reverse=True,
        )[:MAX_RECENT_ACTIVITIES],
        "focus_file": focus_file,
    }
