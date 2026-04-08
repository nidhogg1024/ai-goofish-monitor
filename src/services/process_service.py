"""
进程管理服务
负责管理爬虫进程的启动和停止
"""

import asyncio
import contextlib
import logging
import os
import signal
import sys
from datetime import datetime
from typing import Awaitable, Callable, Dict, TextIO

from src.ai_handler import send_ntfy_notification
from src.config import STATE_FILE
from src.failure_guard import FailureGuard
from src.infrastructure.persistence.sqlite_task_repository import find_task_by_name_sync
from src.infrastructure.config.settings import settings as app_settings
from src.risk_control_guard import GlobalRiskControlGuard
from src.utils import build_task_log_path

logger = logging.getLogger(__name__)

STOP_TIMEOUT_SECONDS = 20
SPIDER_DEBUG_LIMIT_ENV = "SPIDER_DEBUG_LIMIT"
LifecycleHook = Callable[[int], Awaitable[None] | None]


class ProcessService:
    """进程管理服务"""

    def __init__(self):
        self.processes: Dict[int, asyncio.subprocess.Process] = {}
        self.log_paths: Dict[int, str] = {}
        self.log_handles: Dict[int, TextIO] = {}
        self.task_names: Dict[int, str] = {}
        self.exit_watchers: Dict[int, asyncio.Task] = {}
        self.failure_guard = FailureGuard()
        self.global_risk_guard = GlobalRiskControlGuard(
            cooldown_seconds=app_settings.risk_control_cooldown_seconds
        )
        self._on_started: LifecycleHook | None = None
        self._on_stopped: LifecycleHook | None = None

    def set_lifecycle_hooks(
        self,
        *,
        on_started: LifecycleHook | None = None,
        on_stopped: LifecycleHook | None = None,
    ) -> None:
        self._on_started = on_started
        self._on_stopped = on_stopped

    async def _invoke_hook(self, hook: LifecycleHook | None, task_id: int) -> None:
        if hook is None:
            return
        result = hook(task_id)
        if asyncio.iscoroutine(result):
            await result

    def _resolve_cookie_path_sync(self, task_name: str) -> str | None:
        """Best-effort cookie/state path for a task (synchronous)."""
        try:
            task = find_task_by_name_sync(task_name)
            if task and isinstance(task.account_state_file, str) and task.account_state_file.strip():
                return task.account_state_file.strip()
        except Exception:
            pass

        return STATE_FILE if os.path.exists(STATE_FILE) else None

    async def _resolve_cookie_path(self, task_name: str) -> str | None:
        return await asyncio.to_thread(self._resolve_cookie_path_sync, task_name)

    def is_running(self, task_id: int) -> bool:
        """检查任务是否正在运行"""
        process = self.processes.get(task_id)
        return process is not None and process.returncode is None

    def cleanup_finished(self) -> None:
        """清理已退出的进程条目。"""
        finished: list[int] = []
        for task_id, process in self.processes.items():
            if process.returncode is not None:
                finished.append(task_id)
        for task_id in finished:
            process = self.processes.get(task_id)
            if process is not None:
                self._cleanup_runtime(task_id, process)

    def running_count(self) -> int:
        """返回当前仍在运行的任务进程数。"""
        return sum(1 for p in self.processes.values() if p.returncode is None)

    async def _drain_finished_process(self, task_id: int) -> None:
        process = self.processes.get(task_id)
        if process is None or process.returncode is None:
            return

        watcher = self.exit_watchers.get(task_id)
        if watcher is not None:
            await asyncio.shield(watcher)
            return

        self._cleanup_runtime(task_id, process)
        await self._invoke_hook(self._on_stopped, task_id)

    def _open_log_file(self, task_id: int, task_name: str) -> tuple[str, TextIO]:
        # Synchronous open; acceptable because log writes are infrequent and
        # subprocess stdout/stderr need a real file descriptor.
        os.makedirs("logs", exist_ok=True)
        log_file_path = build_task_log_path(task_id, task_name)
        log_file_handle = open(log_file_path, "a", encoding="utf-8")
        return log_file_path, log_file_handle

    def _build_spawn_command(self, task_name: str) -> list[str]:
        command = [
            sys.executable,
            "-u",
            "spider_v2.py",
            "--task-name",
            task_name,
        ]
        debug_limit = str(os.getenv(SPIDER_DEBUG_LIMIT_ENV, "")).strip()
        if debug_limit.isdigit() and int(debug_limit) > 0:
            command.extend(["--debug-limit", debug_limit])
        return command

    async def _spawn_process(
        self,
        task_name: str,
        log_file_handle: TextIO,
    ) -> asyncio.subprocess.Process:
        preexec_fn = os.setsid if sys.platform != "win32" else None
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        return await asyncio.create_subprocess_exec(
            *self._build_spawn_command(task_name),
            stdout=log_file_handle,
            stderr=log_file_handle,
            preexec_fn=preexec_fn,
            env=child_env,
        )

    def _register_runtime(
        self,
        task_id: int,
        task_name: str,
        process: asyncio.subprocess.Process,
        log_file_path: str,
        log_file_handle: TextIO,
    ) -> None:
        self.processes[task_id] = process
        self.log_paths[task_id] = log_file_path
        self.log_handles[task_id] = log_file_handle
        self.task_names[task_id] = task_name
        self.exit_watchers[task_id] = asyncio.create_task(self._watch_process_exit(process))

    async def start_task(self, task_id: int, task_name: str) -> bool:
        """启动任务进程"""
        await self._drain_finished_process(task_id)
        if self.is_running(task_id):
            logger.info("任务 '%s' (ID: %d) 已在运行中", task_name, task_id)
            return False

        global_risk_decision = self.global_risk_guard.should_skip_start()
        if global_risk_decision.skip:
            paused_until = (
                global_risk_decision.cooldown_until.strftime("%Y-%m-%d %H:%M:%S")
                if global_risk_decision.cooldown_until
                else "N/A"
            )
            logger.warning(
                "[GlobalRiskGuard] 跳过启动任务 '%s'，当前处于全局风控冷却期。"
                " 原因: %s; 冷却到: %s",
                task_name, global_risk_decision.reason, paused_until,
            )
            return False

        decision = self.failure_guard.should_skip_start(
            task_name,
            cookie_path=await self._resolve_cookie_path(task_name),
        )
        if decision.skip:
            await self._notify_skip(task_name, decision)
            return False

        log_file_path = ""
        log_file_handle = None
        try:
            log_file_path, log_file_handle = self._open_log_file(task_id, task_name)
            process = await self._spawn_process(task_name, log_file_handle)
        except Exception as exc:
            self._close_log_handle(log_file_handle)
            logger.error("启动任务 '%s' 失败: %s", task_name, exc)
            return False

        self._register_runtime(task_id, task_name, process, log_file_path, log_file_handle)
        logger.info("启动任务 '%s' (PID: %d)", task_name, process.pid)
        await self._invoke_hook(self._on_started, task_id)
        return True

    async def _notify_skip(self, task_name: str, decision) -> None:
        logger.warning(
            "[FailureGuard] 跳过启动任务 '%s'，已暂停重试 "
            "(连续失败 %d/%d)",
            task_name, decision.consecutive_failures, self.failure_guard.threshold,
        )
        if not decision.should_notify:
            return
        try:
            await send_ntfy_notification(
                {
                    "商品标题": f"[任务暂停] {task_name}",
                    "当前售价": "N/A",
                    "商品链接": "#",
                },
                "任务处于暂停状态，将跳过执行。\n"
                f"原因: {decision.reason}\n"
                f"连续失败: {decision.consecutive_failures}/{self.failure_guard.threshold}\n"
                f"暂停到: {decision.paused_until.strftime('%Y-%m-%d %H:%M:%S') if decision.paused_until else 'N/A'}\n"
                "修复方法: 更新登录态/cookies文件后会自动恢复。",
            )
        except Exception as exc:
            logger.error("发送任务暂停通知失败: %s", exc)

    async def _watch_process_exit(self, process: asyncio.subprocess.Process) -> None:
        await process.wait()
        task_id = self._find_task_id_by_process(process)
        if task_id is None:
            return
        self._cleanup_runtime(task_id, process)
        await self._invoke_hook(self._on_stopped, task_id)

    def _find_task_id_by_process(self, process: asyncio.subprocess.Process) -> int | None:
        for task_id, current_process in self.processes.items():
            if current_process is process:
                return task_id
        return None

    def _cleanup_runtime(
        self,
        task_id: int,
        process: asyncio.subprocess.Process,
    ) -> None:
        if self.processes.get(task_id) is not process:
            return
        self.processes.pop(task_id, None)
        self.log_paths.pop(task_id, None)
        self.task_names.pop(task_id, None)
        self._close_log_handle(self.log_handles.pop(task_id, None))
        self.exit_watchers.pop(task_id, None)

    def _close_log_handle(self, log_handle: TextIO | None) -> None:
        if log_handle is None:
            return
        with contextlib.suppress(Exception):
            log_handle.close()

    def _append_stop_marker(self, log_path: str | None) -> None:
        if not log_path:
            return
        try:
            timestamp = datetime.now().strftime(" %Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as log_file:
                log_file.write(f"[{timestamp}] !!! 任务已被终止 !!!\n")
        except Exception as exc:
            logger.error("写入任务终止标记失败: %s", exc)

    async def stop_task(self, task_id: int) -> bool:
        """停止任务进程"""
        await self._drain_finished_process(task_id)
        process = self.processes.get(task_id)
        if process is None:
            logger.debug("任务 ID %d 没有正在运行的进程", task_id)
            return False
        if process.returncode is not None:
            await self._await_exit_watcher(task_id)
            logger.debug("任务进程 %d (ID: %d) 已退出，略过停止", process.pid, task_id)
            return False

        try:
            await self._terminate_process(process, task_id)
            self._append_stop_marker(self.log_paths.get(task_id))
            await self._await_exit_watcher(task_id)
            logger.info("任务进程 %d (ID: %d) 已终止", process.pid, task_id)
            return True
        except ProcessLookupError:
            logger.debug("进程 (ID: %d) 已不存在", task_id)
            return False
        except Exception as exc:
            logger.error("停止任务进程 (ID: %d) 时出错: %s", task_id, exc)
            return False

    async def _terminate_process(
        self,
        process: asyncio.subprocess.Process,
        task_id: int,
    ) -> None:
        if sys.platform != "win32":
            try:
                pgid = os.getpgid(process.pid)
            except ProcessLookupError:
                return
            if pgid == os.getpgrp():
                logger.warning("进程 %d 的 PGID 与当前进程组相同，回退到 process.terminate()", process.pid)
                process.terminate()
            else:
                os.killpg(pgid, signal.SIGTERM)
        else:
            process.terminate()

        try:
            await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT_SECONDS)
            return
        except asyncio.TimeoutError:
            logger.warning(
                "任务进程 %d (ID: %d) 未在 %d 秒内退出，准备强制终止...",
                process.pid, task_id, STOP_TIMEOUT_SECONDS,
            )

        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                pgid = os.getpgid(process.pid)
                if pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    process.kill()
        else:
            process.kill()
        await process.wait()

    async def _await_exit_watcher(self, task_id: int) -> None:
        watcher = self.exit_watchers.get(task_id)
        if watcher is None:
            return
        await asyncio.shield(watcher)

    async def wait_for_task_exit(self, task_id: int) -> None:
        """等待某个任务进程退出。若已退出则立即返回。"""
        await self._drain_finished_process(task_id)
        if not self.is_running(task_id):
            return
        await self._await_exit_watcher(task_id)

    def reindex_after_delete(self, deleted_task_id: int) -> None:
        """删除任务后从运行时索引中移除对应条目。

        不再做 ID 减 1 重排，因为数据库中 task ID 不保证连续。
        只需要移除被删除任务的映射即可。
        """
        for mapping in (
            self.processes,
            self.log_paths,
            self.log_handles,
            self.task_names,
            self.exit_watchers,
        ):
            mapping.pop(deleted_task_id, None)

    async def stop_all(self) -> None:
        """停止所有任务进程"""
        task_ids = list(self.processes.keys())
        for task_id in task_ids:
            await self.stop_task(task_id)
