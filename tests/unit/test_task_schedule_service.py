from src.domain.models.task import Task, TaskGenerateRequest
from src.services.task_schedule_service import (
    assign_scattered_cron,
    rebalance_existing_task_crons,
    resolve_request_cron,
)


def _task(task_id: int, cron: str, *, category: str = "扫地机器人", group_name: str = "租房两猫") -> Task:
    return Task(
        id=task_id,
        task_name=f"任务{task_id}",
        category=category,
        group_name=group_name,
        enabled=True,
        keyword=f"kw-{task_id}",
        description="",
        analyze_images=True,
        max_pages=3,
        personal_only=True,
        min_price=None,
        max_price=None,
        cron=cron,
        ai_prompt_base_file="prompts/base_prompt.txt",
        ai_prompt_criteria_file="prompts/test.txt",
        account_state_file=None,
        account_strategy="auto",
        free_shipping=True,
        new_publish_option=None,
        region=None,
        decision_mode="ai",
        keyword_rules=[],
        is_running=False,
    )


def test_assign_scattered_cron_avoids_busy_group_minute() -> None:
    existing = [
        _task(1, "0 * * * *"),
        _task(2, "5 * * * *"),
        _task(3, "10 * * * *"),
    ]

    resolved = assign_scattered_cron(
        "0 * * * *",
        existing_tasks=existing,
        category="扫地机器人",
        group_name="租房两猫",
    )

    assert resolved == "1 * * * *"


def test_assign_scattered_cron_preserves_custom_expression() -> None:
    existing = [_task(1, "0 * * * *")]

    resolved = assign_scattered_cron(
        "7,37 * * * *",
        existing_tasks=existing,
        category="扫地机器人",
        group_name="租房两猫",
    )

    assert resolved == "7,37 * * * *"


def test_resolve_request_cron_uses_taxonomy_for_scatter() -> None:
    existing = [
        _task(1, "0 * * * *", category="扫地机器人", group_name="租房两猫"),
        _task(2, "1 * * * *", category="扫地机器人", group_name="租房两猫"),
    ]

    req = TaskGenerateRequest(
        task_name="科沃斯 T30",
        keyword="科沃斯 T30",
        description="租房两猫，二手扫地机器人",
        cron="0 * * * *",
    )

    resolved = resolve_request_cron(req, existing_tasks=existing)

    assert resolved == "2 * * * *"


def test_rebalance_existing_task_crons_spreads_default_hourly_jobs() -> None:
    tasks = [
        _task(1, "0 * * * *"),
        _task(2, "0 * * * *"),
        _task(3, "0 * * * *"),
    ]

    rebalanced = rebalance_existing_task_crons(tasks)
    rebalanced_crons = [task.cron for task in rebalanced]

    assert rebalanced_crons == ["1 * * * *", "2 * * * *", "3 * * * *"]
