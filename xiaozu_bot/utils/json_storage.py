import fnmatch
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from nonebot import logger


def plugin_storage(plugin_file: str | Path, name: str = "storage.json") -> Path:
    """Return a plugin-owned storage path resolved from the plugin file.

    Callers can pass ``__file__`` directly. Resolving from the module path
    keeps storage stable when the bot is launched from another directory.
    """
    return Path(plugin_file).resolve().parent / "data" / name


def write_json_atomic(
    path: str | Path,
    payload: Any,
    *,
    indent: int | None = 2,
    ensure_ascii: bool = False,
) -> None:
    """Write JSON through a same-directory temporary file and replace.

    Readers either see the previous complete snapshot or the new complete
    snapshot; they never observe a partially serialized file.  A failed write
    leaves the existing target untouched.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=ensure_ascii, indent=indent)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except OSError:
                logger.warning("atomic JSON temporary cleanup failed: %s", temporary_name)


class JsonRedis:
    """Small JSON-backed subset of the Redis API used by the plugins.

    Values keep their original Python types, unlike Redis with
    ``decode_responses=True``. Supported operations include expiring values,
    hashes, glob-style key lookup, deletion, and automatic persistence.
    """

    def __init__(self, file_path: str | Path, auto_save: bool = True) -> None:
        self.file_path = Path(file_path)
        self.auto_save = auto_save
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load the snapshot without breaking plugin import on bad data.

        A malformed file is moved aside with a ``.broken`` suffix and the
        store starts empty so one damaged plugin file cannot stop the bot.
        """
        if not (self.file_path.exists() and self.file_path.stat().st_size > 0):
            self.data = {}
            if self.auto_save:
                self._save()
            return

        try:
            with self.file_path.open("r", encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, json.JSONDecodeError):
            logger.exception(f"[JsonRedis] 存储文件读不了：{self.file_path}")
            broken = self.file_path.with_suffix(self.file_path.suffix + ".broken")
            try:
                os.replace(self.file_path, broken)
                logger.warning(f"[JsonRedis] 已把坏文件挪到 {broken}，从空的开始")
            except OSError:
                logger.exception("[JsonRedis] 连坏文件都挪不走")
            self.data = {}

    def _save(self) -> None:
        """Persist a complete snapshot through an atomic same-filesystem replace."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.file_path)
        except OSError:
            logger.exception(f"[JsonRedis] 写入失败：{self.file_path}")
            tmp.unlink(missing_ok=True)

    def _clean_expired(self) -> bool:
        """Remove expired values and report whether anything was deleted."""
        now = time.time()
        to_delete = []
        for key, value in self.data.items():
            if isinstance(value, dict) and "_exp" in value and now >= value["_exp"]:
                to_delete.append(key)
        for key in to_delete:
            del self.data[key]
        return bool(to_delete)

    def get(self, key: str) -> Any | None:
        """Return a value, or ``None`` when the key is missing or expired."""
        with self.lock:
            self._clean_expired()
            value = self.data.get(key)
            if isinstance(value, dict) and "_exp" in value:
                return value["_val"]
            return value

    def set(self, key: str, value: Any, ex: int | None = None) -> None:
        """Set a value and optionally expire it after ``ex`` seconds."""
        with self.lock:
            if ex is not None:
                self.data[key] = {"_val": value, "_exp": time.time() + ex}
            else:
                self.data[key] = value
            if self.auto_save:
                self._save()

    def ttl(self, key: str) -> int:
        """Return Redis-compatible remaining lifetime in seconds.

        ``-1`` means a persistent key and ``-2`` means a missing or expired
        key.
        """
        with self.lock:
            self._clean_expired()
            value = self.data.get(key)
            if value is None:
                return -2
            if isinstance(value, dict) and "_exp" in value:
                remaining = value["_exp"] - time.time()
                return int(remaining) if remaining > 0 else -2
            return -1

    def hget(self, name: str, key: str) -> Any | None:
        """Return a field from a hash, or ``None`` when it is unavailable."""
        with self.lock:
            self._clean_expired()
            hash_map = self.data.get(name)
            if isinstance(hash_map, dict):
                return hash_map.get(key)
            return None

    def hset(self, name: str, key: str, value: Any) -> None:
        """Set a field in a hash, creating the hash when needed."""
        with self.lock:
            if name not in self.data or not isinstance(self.data.get(name), dict):
                self.data[name] = {}
            self.data[name][key] = value
            if self.auto_save:
                self._save()

    def _as_hash(self, name: str) -> dict[str, Any]:
        """Return a hash while excluding the internal expiring-value wrapper."""
        value = self.data.get(name)
        if isinstance(value, dict) and "_exp" not in value:
            return value
        return {}

    def hkeys(self, name: str) -> list[str]:
        """Return all field names in a hash."""
        with self.lock:
            self._clean_expired()
            return list(self._as_hash(name).keys())

    def hexists(self, name: str, key: str) -> bool:
        """Return whether a hash contains a field."""
        with self.lock:
            self._clean_expired()
            return key in self._as_hash(name)

    def keys(self, pattern: str = "*") -> list[str]:
        """Return keys matching a glob pattern, like Redis ``KEYS``."""
        with self.lock:
            self._clean_expired()
            return fnmatch.filter(list(self.data.keys()), pattern)

    def exists(self, key: str) -> bool:
        """Return whether a key exists and has not expired."""
        with self.lock:
            self._clean_expired()
            return key in self.data

    def delete(self, key: str) -> None:
        """Delete a key when it exists."""
        with self.lock:
            if key in self.data:
                del self.data[key]
                if self.auto_save:
                    self._save()
