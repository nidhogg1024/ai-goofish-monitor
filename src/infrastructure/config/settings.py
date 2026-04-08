"""
统一配置管理模块
使用 Pydantic 进行类型安全的配置管理
"""
import logging
import os
import secrets
from typing import Optional

from pydantic import Field

# pydantic-settings v2 ships BaseSettings in its own package;
# fall back to pydantic v1 built-in for older stacks.
try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _USING_PYDANTIC_SETTINGS = True
except ImportError:
    from pydantic import BaseSettings  # type: ignore[no-redef]
    _USING_PYDANTIC_SETTINGS = False

from src.services.ai_base_url import normalize_openai_base_url

logger = logging.getLogger(__name__)

DEFAULT_TELEGRAM_API_BASE_URL = "https://api.telegram.org"


def _env_field(default, env_name: str, **kwargs):
    if _USING_PYDANTIC_SETTINGS:
        return Field(default, validation_alias=env_name, **kwargs)
    return Field(default, env=env_name, **kwargs)


if _USING_PYDANTIC_SETTINGS:
    class _EnvSettings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
            protected_namespaces=(),
        )
else:
    class _EnvSettings(BaseSettings):
        class Config:
            env_file = ".env"
            env_file_encoding = "utf-8"
            extra = "ignore"
            protected_namespaces = ()


class AISettings(_EnvSettings):
    """AI模型配置"""
    api_key: Optional[str] = _env_field(None, "OPENAI_API_KEY")
    base_url: str = _env_field("", "OPENAI_BASE_URL")
    model_name: str = _env_field("", "OPENAI_MODEL_NAME")
    proxy_url: Optional[str] = _env_field(None, "PROXY_URL")
    debug_mode: bool = _env_field(False, "AI_DEBUG_MODE")
    enable_response_format: bool = _env_field(True, "ENABLE_RESPONSE_FORMAT")
    enable_thinking: bool = _env_field(False, "ENABLE_THINKING")
    skip_analysis: bool = _env_field(False, "SKIP_AI_ANALYSIS")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = normalize_openai_base_url(self.base_url)

    def is_configured(self) -> bool:
        """检查AI是否已正确配置"""
        return bool(self.base_url and self.model_name)


class NotificationSettings(_EnvSettings):
    """通知服务配置"""
    ntfy_topic_url: Optional[str] = _env_field(None, "NTFY_TOPIC_URL")
    gotify_url: Optional[str] = _env_field(None, "GOTIFY_URL")
    gotify_token: Optional[str] = _env_field(None, "GOTIFY_TOKEN")
    bark_url: Optional[str] = _env_field(None, "BARK_URL")
    wx_bot_url: Optional[str] = _env_field(None, "WX_BOT_URL")
    telegram_bot_token: Optional[str] = _env_field(None, "TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = _env_field(None, "TELEGRAM_CHAT_ID")
    telegram_api_base_url: Optional[str] = _env_field(
        DEFAULT_TELEGRAM_API_BASE_URL,
        "TELEGRAM_API_BASE_URL",
    )
    webhook_url: Optional[str] = _env_field(None, "WEBHOOK_URL")
    webhook_method: str = _env_field("POST", "WEBHOOK_METHOD")
    webhook_headers: Optional[str] = _env_field(None, "WEBHOOK_HEADERS")
    webhook_content_type: str = _env_field("JSON", "WEBHOOK_CONTENT_TYPE")
    webhook_query_parameters: Optional[str] = _env_field(None, "WEBHOOK_QUERY_PARAMETERS")
    webhook_body: Optional[str] = _env_field(None, "WEBHOOK_BODY")
    pcurl_to_mobile: bool = _env_field(True, "PCURL_TO_MOBILE")

    def has_any_notification_enabled(self) -> bool:
        """检查是否配置了任何通知服务"""
        return any([
            self.ntfy_topic_url,
            self.wx_bot_url,
            self.gotify_url and self.gotify_token,
            self.bark_url,
            self.telegram_bot_token and self.telegram_chat_id,
            self.webhook_url
        ])


class ScraperSettings(_EnvSettings):
    """爬虫相关配置"""
    run_headless: bool = _env_field(True, "RUN_HEADLESS")
    login_is_edge: bool = _env_field(False, "LOGIN_IS_EDGE")
    running_in_docker: bool = _env_field(False, "RUNNING_IN_DOCKER")
    state_file: str = _env_field("xianyu_state.json", "STATE_FILE")


class AppSettings(_EnvSettings):
    """应用主配置"""
    server_port: int = _env_field(8000, "SERVER_PORT")
    server_host: str = _env_field("0.0.0.0", "SERVER_HOST")
    cors_origins: str = _env_field("*", "CORS_ORIGINS")
    web_username: str = _env_field("admin", "WEB_USERNAME")
    web_password: str = _env_field("", "WEB_PASSWORD")
    task_log_retention_days: int = _env_field(7, "TASK_LOG_RETENTION_DAYS", ge=1)
    scheduler_max_concurrent_tasks: int = _env_field(2, "SCHEDULER_MAX_CONCURRENT_TASKS", ge=1)
    scheduler_jitter_seconds: int = _env_field(45, "SCHEDULER_JITTER_SECONDS", ge=0)
    scheduler_misfire_grace_seconds: int = _env_field(180, "SCHEDULER_MISFIRE_GRACE_SECONDS", ge=1)
    risk_control_cooldown_seconds: int = _env_field(20 * 60, "RISK_CONTROL_COOLDOWN_SECONDS", ge=60)

    # 文件路径配置
    config_file: str = "config.json"
    image_save_dir: str = "images"
    task_image_dir_prefix: str = "task_images_"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.web_password:
            self.web_password = secrets.token_urlsafe(16)
            logger.warning(
                "WEB_PASSWORD 未配置，已生成随机密码。"
                "请在 .env 中设置 WEB_PASSWORD 以固定密码。"
            )

    def ensure_directories(self) -> None:
        os.makedirs(self.image_save_dir, exist_ok=True)


# 全局配置实例（单例模式）
_settings_instance = None

def get_settings() -> AppSettings:
    """获取全局配置实例"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = AppSettings()
    return _settings_instance


def reload_settings() -> None:
    """重新加载全局配置实例。

    Updates module-level references so that code importing `settings` etc.
    at module load time sees the new values.  Note: any local variable that
    captured the old object before reload will remain stale.
    """
    global _settings_instance, settings, ai_settings, notification_settings, scraper_settings
    from dotenv import load_dotenv
    from src.infrastructure.config.env_manager import env_manager

    load_dotenv(dotenv_path=env_manager.env_file, override=True)
    _settings_instance = None
    settings = get_settings()
    ai_settings = AISettings()
    notification_settings = NotificationSettings()
    scraper_settings = ScraperSettings()


# 导出便捷访问的配置实例
settings = get_settings()
settings.ensure_directories()
ai_settings = AISettings()
notification_settings = NotificationSettings()
scraper_settings = ScraperSettings()
