"""
环境变量管理器
负责读取和更新 .env 文件，并在读取时回退到运行时环境变量
"""
import contextlib
import fcntl
import logging
import os
import re
from typing import Dict, List, Optional
from pathlib import Path

from dotenv import dotenv_values

logger = logging.getLogger(__name__)


_PLAIN_ENV_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_./:-]+$")


class EnvManager:
    """环境变量管理器"""

    def __init__(self, env_file: str = ".env"):
        self.env_file = Path(env_file)
        self._ensure_env_file_exists()

    def _ensure_env_file_exists(self):
        """确保 .env 文件存在"""
        if not self.env_file.exists():
            self.env_file.touch()

    def read_env(self) -> Dict[str, str]:
        """读取所有环境变量"""
        if not self.env_file.exists():
            return {}

        loaded = dotenv_values(self.env_file, encoding="utf-8")
        return {
            key: value
            for key, value in loaded.items()
            if key and value is not None
        }

    def get_value(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """获取单个环境变量的值。

        Priority: os.environ (runtime) > .env file > default.
        Runtime env vars take precedence so that Docker/CI overrides work
        without touching the file.
        """
        runtime_value = os.getenv(key)
        if runtime_value is not None:
            return runtime_value

        env_vars = self.read_env()
        return env_vars.get(key, default)

    def update_values(self, updates: Dict[str, str]) -> bool:
        """批量更新环境变量"""
        return self.apply_changes(updates=updates)

    def apply_changes(
        self,
        updates: Dict[str, str],
        deletions: List[str] | None = None,
    ) -> bool:
        """批量更新并删除环境变量"""
        try:
            existing_vars = self.read_env()
            existing_vars.update(updates)
            for key in deletions or []:
                existing_vars.pop(key, None)
            return self._write_env(existing_vars)
        except Exception as e:
            logger.error("更新环境变量失败: %s", e)
            return False

    def set_value(self, key: str, value: str) -> bool:
        """设置单个环境变量"""
        return self.update_values({key: value})

    def delete_keys(self, keys: List[str]) -> bool:
        """删除指定的环境变量"""
        try:
            existing_vars = self.read_env()
            for key in keys:
                existing_vars.pop(key, None)
            return self._write_env(existing_vars)
        except Exception as e:
            logger.error("删除环境变量失败: %s", e)
            return False

    def _write_env(self, env_vars: Dict[str, str]) -> bool:
        """原子写入环境变量到文件（先写临时文件再 rename）。

        NOTE: Writing rebuilds the file from key-value pairs; any comments,
        blank lines, or ordering from the original file are lost.
        """
        import tempfile
        try:
            dir_path = os.path.dirname(self.env_file) or "."
            fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".env.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    try:
                        for key, value in env_vars.items():
                            f.write(f"{key}={self._serialize_value(value)}\n")
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                os.replace(tmp_path, self.env_file)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise
            return True
        except Exception as e:
            logger.error("写入 .env 文件失败: %s", e)
            return False

    def _serialize_value(self, value: str) -> str:
        """Serialize a value for .env file.

        NOTE: `$` characters in values are NOT escaped; some .env loaders
        (e.g. docker-compose) may interpret them as variable references.
        """
        text = str(value)
        if text == "":
            return ""
        if _PLAIN_ENV_VALUE_PATTERN.fullmatch(text):
            return text
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'


# 全局实例
env_manager = EnvManager()
