"""
新架构的主应用入口
整合所有路由和服务
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import bcrypt as _bcrypt
from pydantic import BaseModel

from src.api.routes import (
    dashboard,
    tasks,
    logs,
    settings,
    prompts,
    results,
    login_state,
    websocket,
    accounts,
    batch_tasks,
)
from src.api.dependencies import (
    set_process_service,
    set_scheduler_service,
    set_task_generation_service,
    set_batch_generation_service,
    set_execution_queue_service,
    get_task_service,
)
from src.services.task_service import TaskService
from src.services.process_service import ProcessService
from src.services.scheduler_service import SchedulerService
from src.services.task_log_cleanup_service import cleanup_task_logs
from src.services.task_generation_service import TaskGenerationService
from src.services.batch_generation_service import BatchGenerationService
from src.services.execution_queue_service import ExecutionQueueService
from src.services.browser_login_service import browser_login_service
from src.services.task_schedule_service import rebalance_existing_task_crons
from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.infrastructure.config.settings import settings as app_settings

logger = logging.getLogger(__name__)

AUTH_EXEMPT_PATHS = {"/health", "/auth/status", "/ws"}


def hash_password(plain: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


_password_hash: str = hash_password(app_settings.web_password)


# 全局服务实例
process_service = ProcessService()
execution_queue_service = ExecutionQueueService(process_service)
scheduler_service = SchedulerService(execution_queue_service)
task_generation_service = TaskGenerationService()
batch_generation_service = BatchGenerationService()


async def _sync_task_runtime_status(task_id: int, is_running: bool) -> None:
    task_service = get_task_service()
    task = await task_service.get_task(task_id)
    if not task or task.is_running == is_running:
        return
    await task_service.update_task_status(task_id, is_running)
    await websocket.broadcast_message(
        "task_status_changed",
        {"id": task_id, "is_running": is_running},
    )


process_service.set_lifecycle_hooks(
    on_started=lambda task_id: _sync_task_runtime_status(task_id, True),
    on_stopped=lambda task_id: _sync_task_runtime_status(task_id, False),
)

# 设置全局 ProcessService 实例供依赖注入使用
set_process_service(process_service)
set_execution_queue_service(execution_queue_service)
set_scheduler_service(scheduler_service)
set_task_generation_service(task_generation_service)
set_batch_generation_service(batch_generation_service)


async def _init_database() -> None:
    await asyncio.to_thread(bootstrap_sqlite_storage)


async def _cleanup_old_logs() -> None:
    await asyncio.to_thread(cleanup_task_logs, keep_days=app_settings.task_log_retention_days)


async def _reset_task_states() -> tuple[list, TaskService, SqliteTaskRepository]:
    task_repo = SqliteTaskRepository()
    task_service = TaskService(task_repo)
    tasks_list = await task_service.get_all_tasks()

    rebalanced_tasks = rebalance_existing_task_crons(tasks_list)
    if any(updated.cron != original.cron for original, updated in zip(tasks_list, rebalanced_tasks)):
        logger.info("正在自动打散已有任务的默认调度时间...")
        persisted_tasks = []
        for original, updated in zip(tasks_list, rebalanced_tasks):
            if updated.cron != original.cron:
                logger.info(
                    "  -> 任务 '%s' 调度已从 '%s' 调整为 '%s'",
                    updated.task_name, original.cron, updated.cron,
                )
                await task_repo.save(updated)
            persisted_tasks.append(updated)
        tasks_list = persisted_tasks

    for task in tasks_list:
        if task.is_running:
            await task_service.update_task_status(task.id, False)

    return tasks_list, task_service, task_repo


async def _start_scheduler(tasks_list: list) -> None:
    await execution_queue_service.start()
    await scheduler_service.reload_jobs(tasks_list)
    scheduler_service.start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("正在启动应用...")
    await _init_database()
    await _cleanup_old_logs()
    tasks_list, _, _ = await _reset_task_states()
    await _start_scheduler(tasks_list)
    logger.info("应用启动完成")

    yield

    logger.info("正在关闭应用...")
    scheduler_service.stop()
    await execution_queue_service.stop()
    await process_service.stop_all()
    await browser_login_service.shutdown()
    logger.info("应用已关闭")


# 创建 FastAPI 应用
app = FastAPI(
    title="闲鱼智能监控机器人",
    description="基于AI的闲鱼商品监控系统",
    version="2.0.0",
    lifespan=lifespan
)

# CORS middleware with configurable origins
_cors_origins = [
    o.strip() for o in app_settings.cors_origins.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Basic Bearer-token / session authentication middleware."""
    path = request.url.path
    if path in AUTH_EXEMPT_PATHS or path.startswith("/static") or path.startswith("/assets"):
        return await call_next(request)
    if request.method == "GET" and not path.startswith("/api/"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token == app_settings.web_username:
            return await call_next(request)

    return await call_next(request)


# 注册路由
app.include_router(tasks.router)
app.include_router(dashboard.router)
app.include_router(logs.router)
app.include_router(settings.router)
app.include_router(prompts.router)
app.include_router(results.router)
app.include_router(login_state.router)
app.include_router(websocket.router)
app.include_router(accounts.router)
app.include_router(batch_tasks.router)

# 挂载静态文件
# 旧的静态文件目录（用于截图等）
app.mount("/static", StaticFiles(directory="static"), name="static")

# 挂载 Vue 3 前端构建产物
# 注意：需要在所有 API 路由之后挂载，以避免覆盖 API 路由
if os.path.exists("dist"):
    app.mount("/assets", StaticFiles(directory="dist/assets"), name="assets")


# 健康检查端点
@app.get("/health")
async def health_check():
    """健康检查（无需认证）"""
    return {"status": "healthy", "message": "服务正常运行"}


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/status")
async def auth_status(payload: LoginRequest):
    """检查认证状态"""
    if payload.username == app_settings.web_username and verify_password(
        payload.password, _password_hash
    ):
        return {"authenticated": True, "username": payload.username}
    raise HTTPException(status_code=401, detail="认证失败")


@app.get("/")
async def read_root(request: Request):
    """提供 Vue 3 SPA 的主页面"""
    if os.path.exists("dist/index.html"):
        return FileResponse("dist/index.html")
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "前端构建产物不存在，请先运行 cd web-ui && npm run build"}
        )


@app.get("/{full_path:path}")
async def serve_spa(request: Request, full_path: str):
    """
    Catch-all 路由，将所有非 API 请求重定向到 index.html
    这样可以支持 Vue Router 的 HTML5 History 模式
    """
    if full_path.endswith(('.ico', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.css', '.js', '.json')):
        return JSONResponse(status_code=404, content={"error": "资源未找到"})

    if os.path.exists("dist/index.html"):
        return FileResponse("dist/index.html")
    else:
        return JSONResponse(
            status_code=500,
            content={"error": "前端构建产物不存在，请先运行 cd web-ui && npm run build"}
        )


if __name__ == "__main__":
    import uvicorn

    logger.info("启动新架构应用，端口: %s", app_settings.server_port)
    uvicorn.run(app, host=app_settings.server_host, port=app_settings.server_port)
