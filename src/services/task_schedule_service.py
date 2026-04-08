"""
任务调度辅助服务。

职责：
- 为新创建任务生成更合理的默认 cron；
- 参考当前已有任务分布，为默认/预设调度自动打散分钟位；
- 保持“用户自定义复杂 cron 不被偷偷改写”。
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Optional

from src.core.cron_utils import normalize_cron_expression
from src.domain.models.task import Task, TaskCreate, TaskGenerateRequest
from src.services.task_taxonomy_service import ensure_task_taxonomy

DEFAULT_AUTO_CRON = "0 * * * *"


def _split_cron_parts(expression: str) -> list[str]:
    return normalize_cron_expression(expression).split()


def _replace_minute(expression: str, minute_expr: str) -> str:
    parts = _split_cron_parts(expression)
    if len(parts) == 5:
        parts[0] = minute_expr
    elif len(parts) == 6:
        parts[1] = minute_expr
    return " ".join(parts)


def _minute_candidates(minute_expr: str) -> list[int]:
    minute_expr = minute_expr.strip()
    if minute_expr == "0":
        return list(range(60))
    if minute_expr.startswith("*/"):
        step = minute_expr[2:]
        if step.isdigit():
            interval = int(step)
            if 1 <= interval <= 60 and 60 % interval == 0:
                return list(range(0, 60, interval))
    return []


def _extract_existing_minutes(expression: Optional[str]) -> list[int]:
    normalized = normalize_cron_expression(expression)
    if not normalized:
        return []
    parts = normalized.split()
    if len(parts) == 5:
        minute_expr = parts[0]
    elif len(parts) == 6:
        minute_expr = parts[1]
    else:
        return []

    if minute_expr.isdigit():
        minute = int(minute_expr)
        if 0 <= minute <= 59:
            return [minute]

    if minute_expr.startswith("*/"):
        candidates = _minute_candidates(minute_expr)
        return candidates[:1] if candidates else []

    return []


def _is_auto_scatter_candidate(expression: Optional[str]) -> bool:
    normalized = normalize_cron_expression(expression)
    if normalized is None:
        return True
    parts = normalized.split()
    if len(parts) not in {5, 6}:
        return False
    minute_expr = parts[0] if len(parts) == 5 else parts[1]
    return bool(_minute_candidates(minute_expr))


def _build_minute_expr(template_expression: str, minute: int) -> str:
    normalized = normalize_cron_expression(template_expression) or DEFAULT_AUTO_CRON
    parts = normalized.split()
    minute_expr = parts[0] if len(parts) == 5 else parts[1]
    if minute_expr.startswith("*/"):
        step = int(minute_expr[2:])
        minute = minute - (minute % step)
        return str(minute)
    return str(minute)


def _score_minute(
    minute: int,
    *,
    global_counts: Counter[int],
    category_counts: Counter[int],
    group_counts: Counter[int],
) -> tuple[int, int, int, int]:
    return (
        group_counts[minute],
        category_counts[minute],
        global_counts[minute],
        minute,
    )


def assign_scattered_cron(
    desired_expression: Optional[str],
    *,
    existing_tasks: Iterable[Task],
    category: Optional[str],
    group_name: Optional[str],
    pending_task_creates: Optional[Iterable[TaskCreate]] = None,
) -> Optional[str]:
    normalized = normalize_cron_expression(desired_expression) if desired_expression else None
    if not _is_auto_scatter_candidate(normalized):
        return normalized

    template_expression = normalized or DEFAULT_AUTO_CRON
    minute_expr = _split_cron_parts(template_expression)[0 if len(_split_cron_parts(template_expression)) == 5 else 1]
    candidates = _minute_candidates(minute_expr)
    if not candidates:
        return template_expression

    global_counts: Counter[int] = Counter()
    category_counts: Counter[int] = Counter()
    group_counts: Counter[int] = Counter()

    def _ingest(expression: Optional[str], task_category: Optional[str], task_group: Optional[str]) -> None:
        for used_minute in _extract_existing_minutes(expression):
            global_counts[used_minute] += 1
            if category and task_category == category:
                category_counts[used_minute] += 1
            if category and group_name and task_category == category and task_group == group_name:
                group_counts[used_minute] += 1

    for task in existing_tasks:
        _ingest(task.cron, task.category, task.group_name)

    for pending in pending_task_creates or []:
        _ingest(pending.cron, pending.category, pending.group_name)

    selected_minute = min(
        candidates,
        key=lambda minute: _score_minute(
            minute,
            global_counts=global_counts,
            category_counts=category_counts,
            group_counts=group_counts,
        ),
    )
    return _replace_minute(template_expression, _build_minute_expr(template_expression, selected_minute))


def resolve_request_cron(
    req: TaskGenerateRequest,
    *,
    existing_tasks: Iterable[Task],
    pending_task_creates: Optional[Iterable[TaskCreate]] = None,
) -> Optional[str]:
    requested = (req.cron or "").strip() or None
    task_name = (req.task_name or req.keyword or "未命名任务").strip()
    keyword = (req.keyword or req.task_name or "").strip()
    category, group_name = ensure_task_taxonomy(
        category=req.category,
        group_name=req.group_name,
        task_name=task_name,
        keyword=keyword,
        description=req.description,
    )
    return assign_scattered_cron(
        requested,
        existing_tasks=existing_tasks,
        category=category,
        group_name=group_name,
        pending_task_creates=pending_task_creates,
    )


def rebalance_existing_task_crons(tasks: Iterable[Task]) -> list[Task]:
    """为当前已有任务重新分配自动调度 cron，避免整点堆积。"""
    original_tasks = sorted(list(tasks), key=lambda item: (item.id or 0, item.task_name))
    rebalanced_tasks: list[Task] = []

    for task in original_tasks:
        other_tasks = [
            existing for existing in original_tasks if existing.id != task.id
        ] + rebalanced_tasks
        resolved_cron = assign_scattered_cron(
            task.cron,
            existing_tasks=other_tasks,
            category=task.category,
            group_name=task.group_name,
        )
        if resolved_cron and resolved_cron != task.cron:
            rebalanced_tasks.append(task.model_copy(update={"cron": resolved_cron}))
        else:
            rebalanced_tasks.append(task)

    return rebalanced_tasks
