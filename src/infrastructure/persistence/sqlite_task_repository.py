"""
基于 SQLite 的任务仓储实现。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import List, Optional

from src.domain.models.task import Task
from src.domain.repositories.task_repository import TaskRepository
from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_connection import sqlite_connection

logger = logging.getLogger(__name__)


def _row_to_task(row) -> Task:
    payload = dict(row)
    payload["enabled"] = bool(payload["enabled"])
    payload["analyze_images"] = bool(payload["analyze_images"])
    payload["personal_only"] = bool(payload["personal_only"])
    payload["free_shipping"] = bool(payload["free_shipping"])
    payload["is_running"] = bool(payload["is_running"])
    payload["keyword_rules"] = json.loads(payload.pop("keyword_rules_json") or "[]")
    if payload.get("first_scan_max_pages") is None:
        payload["first_scan_max_pages"] = 10
    return Task(**payload)


def find_task_by_name_sync(task_name: str) -> Task | None:
    """Standalone helper kept for backward compatibility; delegates to SqliteTaskRepository."""
    return SqliteTaskRepository().find_by_name_sync(task_name)


class SqliteTaskRepository(TaskRepository):
    """基于 SQLite 的任务仓储"""

    def __init__(
        self,
        db_path: str | None = None,
        legacy_config_file: str | None = "config.json",
    ):
        self.db_path = db_path
        self.legacy_config_file = legacy_config_file
        self._bootstrapped = False

    def _ensure_bootstrap(self) -> None:
        if not self._bootstrapped:
            bootstrap_sqlite_storage(
                self.db_path,
                legacy_config_file=self.legacy_config_file,
            )
            self._bootstrapped = True

    def find_by_name_sync(self, task_name: str) -> Task | None:
        self._ensure_bootstrap()
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_name = ? ORDER BY id ASC LIMIT 1",
                (task_name,),
            ).fetchone()
        return _row_to_task(row) if row else None

    async def find_all(self) -> List[Task]:
        return await asyncio.to_thread(self._find_all_sync)

    async def find_by_id(self, task_id: int) -> Optional[Task]:
        return await asyncio.to_thread(self._find_by_id_sync, task_id)

    async def find_by_name(self, task_name: str) -> Optional[Task]:
        return await asyncio.to_thread(find_task_by_name_sync, task_name)

    async def save(self, task: Task) -> Task:
        return await asyncio.to_thread(self._save_sync, task)

    async def delete(self, task_id: int) -> bool:
        return await asyncio.to_thread(self._delete_sync, task_id)

    def _find_all_sync(self) -> List[Task]:
        self._ensure_bootstrap()
        with sqlite_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY id ASC").fetchall()
        return [_row_to_task(row) for row in rows]

    def _find_by_id_sync(self, task_id: int) -> Optional[Task]:
        self._ensure_bootstrap()
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def _save_sync(self, task: Task) -> Task:
        self._ensure_bootstrap()
        with sqlite_connection(self.db_path) as conn:
            task_id = task.id
            payload = self._task_values(task.model_copy(update={"id": task_id}))
            if task_id is None:
                cursor = conn.execute(
                    """
                    INSERT INTO tasks (
                        task_name, category, group_name, enabled, keyword, search_query,
                        description, analyze_images,
                        max_pages, first_scan_max_pages, personal_only, min_price, max_price, cron,
                        ai_prompt_base_file, ai_prompt_criteria_file, account_state_file,
                        account_strategy, free_shipping, new_publish_option, region,
                        decision_mode, keyword_rules_json, is_running
                    ) VALUES (
                        :task_name, :category, :group_name, :enabled, :keyword, :search_query,
                        :description, :analyze_images,
                        :max_pages, :first_scan_max_pages, :personal_only, :min_price, :max_price, :cron,
                        :ai_prompt_base_file, :ai_prompt_criteria_file, :account_state_file,
                        :account_strategy, :free_shipping, :new_publish_option, :region,
                        :decision_mode, :keyword_rules_json, :is_running
                    )
                    """,
                    payload,
                )
                task_id = cursor.lastrowid
            else:
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, task_name, category, group_name, enabled, keyword, search_query,
                        description, analyze_images,
                        max_pages, first_scan_max_pages, personal_only, min_price, max_price, cron,
                        ai_prompt_base_file, ai_prompt_criteria_file, account_state_file,
                        account_strategy, free_shipping, new_publish_option, region,
                        decision_mode, keyword_rules_json, is_running
                    ) VALUES (
                        :id, :task_name, :category, :group_name, :enabled, :keyword, :search_query,
                        :description, :analyze_images,
                        :max_pages, :first_scan_max_pages, :personal_only, :min_price, :max_price, :cron,
                        :ai_prompt_base_file, :ai_prompt_criteria_file, :account_state_file,
                        :account_strategy, :free_shipping, :new_publish_option, :region,
                        :decision_mode, :keyword_rules_json, :is_running
                    ) ON CONFLICT(id) DO UPDATE SET
                        task_name=excluded.task_name, category=excluded.category,
                        group_name=excluded.group_name, enabled=excluded.enabled,
                        keyword=excluded.keyword, search_query=excluded.search_query,
                        description=excluded.description, analyze_images=excluded.analyze_images,
                        max_pages=excluded.max_pages, first_scan_max_pages=excluded.first_scan_max_pages,
                        personal_only=excluded.personal_only, min_price=excluded.min_price,
                        max_price=excluded.max_price, cron=excluded.cron,
                        ai_prompt_base_file=excluded.ai_prompt_base_file,
                        ai_prompt_criteria_file=excluded.ai_prompt_criteria_file,
                        account_state_file=excluded.account_state_file,
                        account_strategy=excluded.account_strategy,
                        free_shipping=excluded.free_shipping,
                        new_publish_option=excluded.new_publish_option,
                        region=excluded.region, decision_mode=excluded.decision_mode,
                        keyword_rules_json=excluded.keyword_rules_json,
                        is_running=excluded.is_running
                    """,
                    payload,
                )
            conn.commit()
        return task.model_copy(update={"id": task_id})

    def _delete_sync(self, task_id: int) -> bool:
        self._ensure_bootstrap()
        with sqlite_connection(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
        return cursor.rowcount > 0

    def _task_values(self, task: Task) -> dict:
        values = task.model_dump()
        values["enabled"] = int(task.enabled)
        values["analyze_images"] = int(task.analyze_images)
        values["personal_only"] = int(task.personal_only)
        values["free_shipping"] = int(task.free_shipping)
        values["is_running"] = int(task.is_running)
        values["keyword_rules_json"] = json.dumps(task.keyword_rules or [], ensure_ascii=False)
        values.pop("keyword_rules", None)
        return values
