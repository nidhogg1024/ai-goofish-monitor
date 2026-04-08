"""
SQLite 持久化相关的统一命名规则。
"""
from __future__ import annotations

import hashlib


DEFAULT_DATABASE_PATH = "data/app.sqlite3"
RESULT_FILE_SUFFIX = "_full_data.jsonl"


def build_result_filename(keyword: str) -> str:
    return f"{str(keyword or '').replace(' ', '_')}{RESULT_FILE_SUFFIX}"


def normalize_keyword_from_filename(filename: str) -> str:
    return str(filename or "").replace(RESULT_FILE_SUFFIX, "")


def normalize_keyword_slug(keyword: str) -> str:
    """Build a filesystem/DB-safe slug from a keyword.

    Special characters are filtered, which may cause different keywords
    to produce the same slug.  A short hash suffix is appended when the
    raw keyword contains characters outside the safe set to reduce
    collision likelihood.
    """
    raw = str(keyword or "").lower().replace(" ", "_")
    text = "".join(
        char for char in raw
        if char.isalnum() or char in "_-"
    ).rstrip("_")
    if not text:
        return "unknown"
    if text != raw:
        suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        text = f"{text}_{suffix}"
    return text
