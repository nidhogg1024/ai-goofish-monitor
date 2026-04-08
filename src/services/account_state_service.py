"""
账号状态文件管理
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

from src.infrastructure.config.env_manager import env_manager

logger = logging.getLogger(__name__)

_MAX_FILENAME_ATTEMPTS = 10000


class AccountError(Exception):
    """账号业务异常基类"""
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


MANIFEST_FILENAME = ".accounts.json"
SAFE_STEM_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
INVALID_DISPLAY_CHARS_RE = re.compile(r"[\\/\x00-\x1f\x7f]")


def _strip_quotes(value: str) -> str:
    if not value:
        return value
    if value.startswith(("\"", "'")) and value.endswith(("\"", "'")):
        return value[1:-1]
    return value


def get_state_dir() -> Path:
    raw = env_manager.get_value("ACCOUNT_STATE_DIR", "state") or "state"
    return Path(_strip_quotes(raw.strip()))


def ensure_state_dir() -> Path:
    state_dir = get_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def validate_display_name(name: str) -> str:
    trimmed = str(name or "").strip()
    if not trimmed:
        raise AccountError("账号名称不能为空。", status_code=400)
    if len(trimmed) > 50:
        raise AccountError("账号名称最多 50 个字符。", status_code=400)
    if INVALID_DISPLAY_CHARS_RE.search(trimmed):
        raise AccountError("账号名称不能包含斜杠或控制字符。", status_code=400)
    return trimmed


def _manifest_path() -> Path:
    return ensure_state_dir() / MANIFEST_FILENAME


def _load_manifest() -> Dict[str, str]:
    manifest_path = _manifest_path()
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("加载账号清单文件失败: %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    manifest: Dict[str, str] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str) and value.endswith(".json"):
            manifest[key] = value
    return manifest


def _save_manifest(manifest: Dict[str, str]) -> None:
    manifest_path = _manifest_path()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _iter_state_files() -> List[Path]:
    state_dir = ensure_state_dir()
    return sorted(
        path
        for path in state_dir.glob("*.json")
        if path.name != MANIFEST_FILENAME
    )


def _build_filename(display_name: str, existing_filenames: set[str]) -> str:
    if SAFE_STEM_RE.fullmatch(display_name):
        candidate = f"{display_name}.json"
        if candidate not in existing_filenames:
            return candidate

    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", display_name).strip("-_").lower()
    normalized = normalized[:24] if normalized else "account"
    suffix = hashlib.sha1(display_name.encode("utf-8")).hexdigest()[:8]
    candidate = f"{normalized}-{suffix}.json"
    if candidate not in existing_filenames:
        return candidate

    for index in range(2, 2 + _MAX_FILENAME_ATTEMPTS):
        fallback = f"{normalized}-{suffix}-{index}.json"
        if fallback not in existing_filenames:
            return fallback
    raise AccountError("无法生成唯一文件名", status_code=500)


def list_account_entries() -> List[dict]:
    state_dir = ensure_state_dir()
    manifest = _load_manifest()
    entries: List[dict] = []
    seen_files: set[str] = set()

    for display_name in sorted(manifest.keys()):
        filename = manifest[display_name]
        path = state_dir / filename
        if not path.exists():
            continue
        entries.append({"name": display_name, "path": str(path)})
        seen_files.add(filename)

    for path in _iter_state_files():
        if path.name in seen_files:
            continue
        entries.append({"name": path.stem, "path": str(path)})

    entries.sort(key=lambda item: item["name"])
    return entries


def resolve_account(display_name: str) -> Tuple[str, Path]:
    account_name = validate_display_name(display_name)
    state_dir = ensure_state_dir()
    manifest = _load_manifest()

    filename = manifest.get(account_name)
    if filename:
        path = state_dir / filename
        if path.exists():
            return account_name, path

    legacy_path = state_dir / f"{account_name}.json"
    if legacy_path.exists():
        return account_name, legacy_path

    raise AccountError("账号不存在", status_code=404)


def create_account_entry(display_name: str) -> Tuple[str, Path]:
    account_name = validate_display_name(display_name)
    filename, path = prepare_account_path(account_name)
    manifest = _load_manifest()
    manifest[account_name] = filename
    _save_manifest(manifest)
    return account_name, path


def prepare_account_path(display_name: str) -> Tuple[str, Path]:
    account_name = validate_display_name(display_name)
    state_dir = ensure_state_dir()
    manifest = _load_manifest()

    if account_name in manifest:
        raise AccountError("账号已存在", status_code=409)

    legacy_path = state_dir / f"{account_name}.json"
    if legacy_path.exists():
        raise AccountError("账号已存在", status_code=409)

    existing_filenames = {path.name for path in _iter_state_files()}
    filename = _build_filename(account_name, existing_filenames)
    path = state_dir / filename
    return filename, path


def register_account_path(display_name: str, path: Path) -> Tuple[str, Path]:
    account_name = validate_display_name(display_name)
    state_dir = ensure_state_dir()
    if path.parent != state_dir:
        raise AccountError("账号文件必须保存在 state 目录下。", status_code=400)

    manifest = _load_manifest()
    existing_path = manifest.get(account_name)
    if existing_path and existing_path != path.name:
        raise AccountError("账号已存在", status_code=409)

    legacy_path = state_dir / f"{account_name}.json"
    if legacy_path.exists() and legacy_path.name != path.name:
        raise AccountError("账号已存在", status_code=409)

    manifest[account_name] = path.name
    _save_manifest(manifest)
    return account_name, path


def delete_account_entry(display_name: str) -> Path:
    """Remove the account from the manifest and return its file path.
    NOTE: The actual state file on disk is NOT deleted; callers should
    remove it explicitly if needed.
    """
    account_name = validate_display_name(display_name)
    state_dir = ensure_state_dir()
    manifest = _load_manifest()

    filename = manifest.pop(account_name, None)
    if filename:
        _save_manifest(manifest)
        return state_dir / filename

    legacy_path = state_dir / f"{account_name}.json"
    if legacy_path.exists():
        return legacy_path

    raise AccountError("账号不存在", status_code=404)
