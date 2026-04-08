"""
结果文件管理路由
"""
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from urllib.parse import quote

logger = logging.getLogger(__name__)

_SORT_BY_WHITELIST = {"crawl_time", "price", "title", "item_id", "created_at", "keyword_hit_count"}

from src.services.price_history_service import build_price_history_insights
from src.domain.models.task import Task
from src.services.result_export_service import build_results_csv
from src.services.result_file_service import (
    enrich_records_with_dynamic_price_insight,
    enrich_records_with_price_insight,
    validate_result_filename,
)
from src.services.result_storage_service import (
    build_result_ndjson,
    delete_result_file_records,
    list_result_filenames,
    load_all_result_records,
    load_all_result_records_by_scope,
    query_result_records_by_scope,
    query_result_records,
    result_file_exists,
)
from src.services.price_history_service import build_price_history_insights_for_keywords
from src.api.dependencies import get_task_service
from src.services.task_service import TaskService


router = APIRouter(prefix="/api/results", tags=["results"])

DEFAULT_EXPORT_FILENAME = "export.csv"


def _validate_sort_by(sort_by: str) -> str:
    if sort_by not in _SORT_BY_WHITELIST:
        raise HTTPException(
            status_code=400,
            detail=f"无效排序字段: {sort_by}，允许值: {', '.join(sorted(_SORT_BY_WHITELIST))}",
        )
    return sort_by


def _resolve_recommended_flags(
    recommended_only: bool,
    ai_recommended_only: bool,
    keyword_recommended_only: bool,
) -> tuple[bool, bool]:
    """Resolve legacy recommended_only into specific flags. Returns (ai, keyword)."""
    if ai_recommended_only and keyword_recommended_only:
        raise HTTPException(status_code=400, detail="AI推荐筛选与关键词推荐筛选不能同时开启。")
    if recommended_only and not ai_recommended_only and not keyword_recommended_only:
        ai_recommended_only = True
    return ai_recommended_only, keyword_recommended_only


def _validate_result_path(filename: str) -> None:
    """Strict path traversal validation for result filenames."""
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="非法的文件路径")
    resolved = os.path.realpath(os.path.join("results", filename))
    if not resolved.startswith(os.path.realpath("results")):
        raise HTTPException(status_code=400, detail="非法的文件路径")


def _build_download_headers(export_name: str) -> dict[str, str]:
    ascii_name = export_name.encode("ascii", "ignore").decode("ascii")
    if ascii_name != export_name or not ascii_name:
        ascii_name = DEFAULT_EXPORT_FILENAME
    encoded_name = quote(export_name, safe="")
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{encoded_name}"
        )
    }


def _filter_tasks_by_scope(
    tasks: list[Task],
    *,
    category: str | None,
    group_name: str | None,
    task_name: str | None,
) -> list[Task]:
    filtered = tasks
    if category:
        filtered = [task for task in filtered if (task.category or "") == category]
    if group_name:
        filtered = [task for task in filtered if (task.group_name or "") == group_name]
    if task_name:
        filtered = [task for task in filtered if task.task_name == task_name]
    return filtered


def _build_scope_payload(tasks: list[Task]) -> dict[str, list[str]]:
    keywords = [str(task.keyword or "").strip() for task in tasks if str(task.keyword or "").strip()]
    task_names = [str(task.task_name or "").strip() for task in tasks if str(task.task_name or "").strip()]
    return {
        "keywords": keywords,
        "task_names": task_names,
    }


@router.get("/query")
async def query_results_by_scope(
    category: str | None = Query(None),
    group_name: str | None = Query(None),
    task_name: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    recommended_only: bool = Query(False),
    ai_recommended_only: bool = Query(False),
    keyword_recommended_only: bool = Query(False),
    sort_by: str = Query("crawl_time"),
    sort_order: str = Query("desc"),
    task_service: TaskService = Depends(get_task_service),
):
    _validate_sort_by(sort_by)
    ai_recommended_only, keyword_recommended_only = _resolve_recommended_flags(
        recommended_only, ai_recommended_only, keyword_recommended_only,
    )

    tasks = await task_service.get_all_tasks()
    scoped_tasks = _filter_tasks_by_scope(
        tasks,
        category=category,
        group_name=group_name,
        task_name=task_name,
    )
    if not scoped_tasks:
        return {"total_items": 0, "page": page, "limit": limit, "items": []}
    scope = _build_scope_payload(scoped_tasks)
    total_items, items = await query_result_records_by_scope(
        keywords=scope["keywords"],
        task_names=scope["task_names"],
        ai_recommended_only=ai_recommended_only,
        keyword_recommended_only=keyword_recommended_only,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        limit=limit,
    )
    return {
        "total_items": total_items,
        "page": page,
        "limit": limit,
        "items": enrich_records_with_dynamic_price_insight(items),
    }


@router.get("/query/insights")
async def query_result_insights_by_scope(
    category: str | None = Query(None),
    group_name: str | None = Query(None),
    task_name: str | None = Query(None),
    task_service: TaskService = Depends(get_task_service),
):
    tasks = await task_service.get_all_tasks()
    scoped_tasks = _filter_tasks_by_scope(
        tasks,
        category=category,
        group_name=group_name,
        task_name=task_name,
    )
    if not scoped_tasks:
        return {
            "market_summary": {
                "sample_count": 0,
                "avg_price": None,
                "median_price": None,
                "min_price": None,
                "max_price": None,
                "snapshot_time": None,
            },
            "history_summary": {
                "unique_items": 0,
                "sample_count": 0,
                "avg_price": None,
                "median_price": None,
                "min_price": None,
                "max_price": None,
            },
            "daily_trend": [],
            "latest_snapshot_at": None,
        }
    keywords = [str(task.keyword or "").strip() for task in scoped_tasks if str(task.keyword or "").strip()]
    return build_price_history_insights_for_keywords(keywords)


@router.get("/files")
async def get_result_files():
    """获取所有结果文件列表"""
    return {"files": await list_result_filenames()}


@router.get("/files/{filename:path}")
async def download_result_file(filename: str):
    """下载指定的结果文件"""
    _validate_result_path(filename)
    if not filename.endswith(".jsonl") or not await result_file_exists(filename):
        raise HTTPException(status_code=404, detail="文件不存在")
    return Response(
        content=await build_result_ndjson(filename),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/files/{filename:path}")
async def delete_result_file(filename: str):
    """删除指定的结果文件"""
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="非法的文件路径")
    if not filename.endswith(".jsonl"):
        raise HTTPException(status_code=400, detail="只能删除 .jsonl 文件")
    deleted_rows = await delete_result_file_records(filename)
    if deleted_rows <= 0:
        raise HTTPException(status_code=404, detail="文件不存在")
    return {"message": f"文件 {filename} 已成功删除"}


@router.get("/{filename}")
async def get_result_file_content(
    filename: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    recommended_only: bool = Query(False),  # 兼容旧参数，等价于 ai_recommended_only
    ai_recommended_only: bool = Query(False),
    keyword_recommended_only: bool = Query(False),
    sort_by: str = Query("crawl_time"),
    sort_order: str = Query("desc"),
):
    """读取指定的 .jsonl 文件内容，支持分页、筛选和排序"""
    _validate_sort_by(sort_by)
    ai_recommended_only, keyword_recommended_only = _resolve_recommended_flags(
        recommended_only, ai_recommended_only, keyword_recommended_only,
    )

    try:
        validate_result_filename(filename)
        total_items, items = await query_result_records(
            filename,
            ai_recommended_only=ai_recommended_only,
            keyword_recommended_only=keyword_recommended_only,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取结果文件时出错: {exc}")
    if total_items <= 0 and not await result_file_exists(filename):
        raise HTTPException(status_code=404, detail="结果文件未找到")
    paginated_results = await enrich_records_with_price_insight(items, filename)

    return {
        "total_items": total_items,
        "page": page,
        "limit": limit,
        "items": paginated_results
    }


@router.get("/{filename}/insights")
async def get_result_file_insights(filename: str):
    try:
        validate_result_filename(filename)
        keyword = filename.replace("_full_data.jsonl", "")
        return build_price_history_insights(keyword)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{filename}/export")
async def export_result_file_content(
    filename: str,
    recommended_only: bool = Query(False),
    ai_recommended_only: bool = Query(False),
    keyword_recommended_only: bool = Query(False),
    sort_by: str = Query("crawl_time"),
    sort_order: str = Query("desc"),
):
    _validate_sort_by(sort_by)
    ai_recommended_only, keyword_recommended_only = _resolve_recommended_flags(
        recommended_only, ai_recommended_only, keyword_recommended_only,
    )

    results = []
    try:
        validate_result_filename(filename)
        results = await load_all_result_records(
            filename,
            ai_recommended_only=ai_recommended_only,
            keyword_recommended_only=keyword_recommended_only,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        csv_text = build_results_csv(
            await enrich_records_with_price_insight(results, filename)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("导出结果文件时出错: %s", exc)
        raise HTTPException(status_code=500, detail="导出结果文件时出错")
    if not results and not await result_file_exists(filename):
        raise HTTPException(status_code=404, detail="结果文件未找到")

    export_name = filename.replace(".jsonl", ".csv")
    headers = _build_download_headers(export_name)
    return Response(content=csv_text, media_type="text/csv; charset=utf-8", headers=headers)
