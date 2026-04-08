"""Global risk-control cooldown guard.

目标:
- 任意任务命中闲鱼风控/验证页后，短时间内暂停后续任务启动。
- 让系统先等待人工验证与会话恢复，避免队列继续把后续任务全部撞进同一类错误。

说明:
- 仅使用标准库，主进程和爬虫子进程都能读写同一个状态文件。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _write_json(path: str, payload: dict) -> None:
    _ensure_parent(path)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class GlobalRiskDecision:
    skip: bool
    reason: str
    cooldown_until: Optional[datetime]


class GlobalRiskControlGuard:
    def __init__(self, path: str = "logs/global-risk-control.json", cooldown_seconds: int = 20 * 60):
        self.path = path
        self.cooldown_seconds = max(60, int(cooldown_seconds))

    def snapshot(self) -> dict:
        data = _read_json(self.path)
        cooldown_until = _parse_dt(data.get("cooldown_until"))
        return {
            "active": bool(cooldown_until and cooldown_until > datetime.now()),
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "reason": data.get("reason"),
            "task_name": data.get("task_name"),
            "keyword": data.get("keyword"),
            "updated_at": data.get("updated_at"),
        }

    def should_skip_start(self) -> GlobalRiskDecision:
        data = _read_json(self.path)
        cooldown_until = _parse_dt(data.get("cooldown_until"))
        if cooldown_until and cooldown_until > datetime.now():
            return GlobalRiskDecision(
                skip=True,
                reason=(data.get("reason") or "检测到风控，等待人工处理"),
                cooldown_until=cooldown_until,
            )
        return GlobalRiskDecision(skip=False, reason="", cooldown_until=None)

    def activate(self, *, task_name: str, keyword: str, reason: str) -> None:
        now = datetime.now()
        payload = {
            "active": True,
            "task_name": task_name,
            "keyword": keyword,
            "reason": reason,
            "updated_at": now.isoformat(),
            "cooldown_until": (now + timedelta(seconds=self.cooldown_seconds)).isoformat(),
        }
        _write_json(self.path, payload)

    def clear(self) -> None:
        payload = {
            "active": False,
            "task_name": None,
            "keyword": None,
            "reason": None,
            "updated_at": datetime.now().isoformat(),
            "cooldown_until": None,
        }
        _write_json(self.path, payload)
