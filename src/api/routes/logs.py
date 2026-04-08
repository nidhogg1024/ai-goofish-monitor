"""
日志管理路由
"""
import logging
import os
import re
from datetime import datetime
from typing import Optional, Tuple, List
import aiofiles
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from src.api.dependencies import get_task_service
from src.services.task_service import TaskService
from src.utils import resolve_task_log_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["logs"])
LOG_TIMESTAMP_RE = re.compile(r"^\[\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")

MAX_TASK_IDS_PER_REQUEST = 50
MAX_LOG_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


def _parse_task_ids_param(task_id: Optional[int], task_ids: Optional[str]) -> list[int]:
    resolved: list[int] = []
    if task_ids:
        for part in task_ids.split(","):
            value = part.strip()
            if value.isdigit():
                resolved.append(int(value))
            if len(resolved) >= MAX_TASK_IDS_PER_REQUEST:
                break
    if task_id is not None:
        resolved.append(task_id)
    return sorted(set(resolved))[:MAX_TASK_IDS_PER_REQUEST]


def _extract_line_timestamp(line: str) -> datetime | None:
    match = LOG_TIMESTAMP_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


async def _read_all_log_lines(log_file_path: str) -> list[str]:
    try:
        file_size = os.path.getsize(log_file_path)
        if file_size > MAX_LOG_FILE_BYTES:
            logger.warning(
                "日志文件 %s 过大 (%d bytes)，只读取最后 %d bytes",
                log_file_path, file_size, MAX_LOG_FILE_BYTES,
            )
            async with aiofiles.open(log_file_path, 'rb') as f:
                await f.seek(max(0, file_size - MAX_LOG_FILE_BYTES))
                raw = await f.read()
            content = raw.decode('utf-8', errors='replace')
        else:
            async with aiofiles.open(log_file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = await f.read()
    except (OSError, IOError) as e:
        logger.warning("读取日志文件失败 %s: %s", log_file_path, e)
        return []
    if not content:
        return []
    return content.splitlines()


async def _load_task_logs(task_service: TaskService, task_ids: list[int]) -> list[tuple[int, str, list[str], int]]:
    task_logs: list[tuple[int, str, list[str], int]] = []
    for current_task_id in task_ids:
        task = await task_service.get_task(current_task_id)
        if not task:
            continue
        log_file_path = resolve_task_log_path(current_task_id, task.task_name)
        try:
            if not os.path.exists(log_file_path):
                task_logs.append((current_task_id, task.task_name, [], 0))
                continue
            lines = await _read_all_log_lines(log_file_path)
            file_size = os.path.getsize(log_file_path)
            task_logs.append((current_task_id, task.task_name, lines, file_size))
        except (OSError, IOError) as e:
            logger.warning("读取任务 %d 日志失败: %s", current_task_id, e)
            task_logs.append((current_task_id, task.task_name, [], 0))
    return task_logs


def _build_group_tail_response(
    task_logs: list[tuple[int, str, list[str], int]],
    offset_lines: int,
    limit_lines: int,
) -> dict:
    merged_lines: list[tuple[datetime | None, int, str]] = []
    total_size = 0
    for task_id, task_name, lines, file_size in task_logs:
        total_size += file_size
        for line in lines:
            merged_lines.append((
                _extract_line_timestamp(line),
                task_id,
                f"[{task_name}] {line}",
            ))

    merged_lines.sort(key=lambda item: (item[0] or datetime.min, item[1], item[2]))
    all_lines = [line for _, _, line in merged_lines]
    if limit_lines <= 0:
        return {"content": "", "has_more": False, "next_offset": offset_lines, "new_pos": total_size}

    end = max(0, len(all_lines) - offset_lines)
    start = max(0, end - limit_lines)
    selected = all_lines[start:end]
    has_more = start > 0
    next_offset = offset_lines + len(selected)
    return {
        "content": "\n".join(selected),
        "has_more": has_more,
        "next_offset": next_offset,
        "new_pos": total_size,
    }


async def _read_tail_lines(
    log_file_path: str,
    offset_lines: int,
    limit_lines: int,
    chunk_size: int = 8192
) -> Tuple[List[str], bool, int]:
    async with aiofiles.open(log_file_path, 'rb') as f:
        await f.seek(0, os.SEEK_END)
        file_size = await f.tell()

        if file_size == 0 or limit_lines <= 0:
            return [], False, file_size

        offset_lines = max(0, int(offset_lines))
        limit_lines = max(0, int(limit_lines))
        lines_needed = offset_lines + limit_lines

        pos = file_size
        buffer = b""
        lines: List[bytes] = []

        while pos > 0 and len(lines) < lines_needed:
            read_size = min(chunk_size, pos)
            pos -= read_size
            await f.seek(pos)
            chunk = await f.read(read_size)
            buffer = chunk + buffer
            lines = buffer.splitlines()

        start = max(0, len(lines) - lines_needed)
        end = max(0, len(lines) - offset_lines)
        selected = lines[start:end] if end > start else []

        has_more = pos > 0 or len(lines) > lines_needed
        decoded = [line.decode('utf-8', errors='replace') for line in selected]
        return decoded, has_more, file_size


@router.get("")
async def get_logs(
    from_pos: int = 0,
    task_id: Optional[int] = Query(default=None, ge=0),
    task_ids: Optional[str] = Query(default=None),
    task_service: TaskService = Depends(get_task_service),
):
    """获取日志内容（增量读取）"""
    resolved_task_ids = _parse_task_ids_param(task_id, task_ids)
    if not resolved_task_ids:
        return JSONResponse(content={
            "new_content": "请选择任务后查看日志。",
            "new_pos": 0
        })

    if len(resolved_task_ids) > 1:
        task_logs = await _load_task_logs(task_service, resolved_task_ids)
        response = _build_group_tail_response(task_logs, offset_lines=0, limit_lines=200)
        return {
            "new_content": response["content"],
            "new_pos": response["new_pos"],
        }

    single_task_id = resolved_task_ids[0] if resolved_task_ids else task_id
    if single_task_id is None:
        return JSONResponse(content={
            "new_content": "请选择任务后查看日志。",
            "new_pos": 0
        })
    task = await task_service.get_task(single_task_id)
    if not task:
        return JSONResponse(status_code=404, content={
            "new_content": "任务不存在或已删除。",
            "new_pos": 0
        })

    log_file_path = resolve_task_log_path(single_task_id, task.task_name)

    if not os.path.exists(log_file_path):
        return JSONResponse(content={
            "new_content": "",
            "new_pos": 0
        })

    try:
        async with aiofiles.open(log_file_path, 'rb') as f:
            await f.seek(0, os.SEEK_END)
            file_size = await f.tell()

            if from_pos >= file_size:
                return {"new_content": "", "new_pos": file_size}

            await f.seek(from_pos)
            new_bytes = await f.read()

        new_content = new_bytes.decode('utf-8', errors='replace')
        return {"new_content": new_content, "new_pos": file_size}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"new_content": f"\n读取日志文件时出错: {e}", "new_pos": from_pos}
        )  


@router.get("/tail")
async def get_logs_tail(
    task_id: Optional[int] = Query(default=None, ge=0),
    task_ids: Optional[str] = Query(default=None),
    offset_lines: int = Query(default=0, ge=0),
    limit_lines: int = Query(default=50, ge=1, le=1000),
    task_service: TaskService = Depends(get_task_service),
):
    """获取日志尾部内容（按行分页）"""
    resolved_task_ids = _parse_task_ids_param(task_id, task_ids)
    if not resolved_task_ids:
        return JSONResponse(content={
            "content": "",
            "has_more": False,
            "next_offset": 0,
            "new_pos": 0
        })

    if len(resolved_task_ids) > 1:
        task_logs = await _load_task_logs(task_service, resolved_task_ids)
        return _build_group_tail_response(task_logs, offset_lines=offset_lines, limit_lines=limit_lines)

    task = await task_service.get_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={
            "content": "",
            "has_more": False,
            "next_offset": 0,
            "new_pos": 0
        })

    log_file_path = resolve_task_log_path(task_id, task.task_name)

    if not os.path.exists(log_file_path):
        return JSONResponse(content={
            "content": "",
            "has_more": False,
            "next_offset": 0,
            "new_pos": 0
        })

    try:
        lines, has_more, file_size = await _read_tail_lines(
            log_file_path,
            offset_lines=offset_lines,
            limit_lines=limit_lines
        )
        next_offset = offset_lines + len(lines)
        return {
            "content": "\n".join(lines),
            "has_more": has_more,
            "next_offset": next_offset,
            "new_pos": file_size
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "content": f"读取日志文件时出错: {e}",
                "has_more": False,
                "next_offset": offset_lines,
                "new_pos": 0
            }
        )


@router.delete("", response_model=dict)
async def clear_logs(
    task_id: Optional[int] = Query(default=None, ge=0),
    task_ids: Optional[str] = Query(default=None),
    task_service: TaskService = Depends(get_task_service),
):
    """清空日志文件"""
    resolved_task_ids = _parse_task_ids_param(task_id, task_ids)
    if not resolved_task_ids:
        return {"message": "未指定任务，无法清空日志。"}

    if len(resolved_task_ids) > 1:
        cleared = 0
        for current_task_id in resolved_task_ids:
            task = await task_service.get_task(current_task_id)
            if not task:
                continue
            log_file_path = resolve_task_log_path(current_task_id, task.task_name)
            if not os.path.exists(log_file_path):
                continue
            async with aiofiles.open(log_file_path, 'w', encoding='utf-8') as f:
                await f.write("")
            cleared += 1
        return {"message": f"已清空 {cleared} 个任务日志。"}

    task = await task_service.get_task(task_id)
    if not task:
        return {"message": "任务不存在或已删除。"}

    log_file_path = resolve_task_log_path(task_id, task.task_name)

    if not os.path.exists(log_file_path):
        return {"message": "日志文件不存在，无需清空。"}

    try:
        async with aiofiles.open(log_file_path, 'w', encoding='utf-8') as f:
            await f.write("")
        return {"message": "日志已成功清空。"}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"message": f"清空日志文件时出错: {e}"}
        )
