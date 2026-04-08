"""
任务运行日志清理服务。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PATTERNS = ("*.log", "*.log.gz")


def cleanup_task_logs(
    logs_dir: str = "logs",
    *,
    keep_days: int = 7,
    now: datetime | None = None,
) -> list[str]:
    if keep_days < 1:
        logger.warning("任务日志清理已跳过：保留天数配置无效 (%d)", keep_days)
        return []

    root = Path(logs_dir)
    if not root.exists():
        return []

    current_time = now or datetime.now(timezone.utc)
    tz = current_time.tzinfo
    cutoff = current_time - timedelta(days=keep_days)
    removed_files: list[str] = []

    for pattern in _LOG_PATTERNS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=tz)
            except OSError as exc:
                logger.warning("读取任务日志时间失败，已跳过: %s (%s)", path, exc)
                continue

            if modified_at >= cutoff:
                continue

            try:
                path.unlink()
                removed_files.append(str(path))
            except OSError as exc:
                logger.warning("删除历史任务日志失败，已跳过: %s (%s)", path, exc)

    if removed_files:
        logger.info(
            "任务日志清理完成：已删除 %d 个超过 %d 天的历史日志文件。",
            len(removed_files), keep_days,
        )

    return removed_files
