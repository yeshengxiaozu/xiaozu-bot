import json
import threading
import time
from pathlib import Path
from typing import Any, Optional


class JsonRedis:
    """基于 JSON 文件的键值存储，模拟 Redis 的常用 API。

    支持:
    - get/set 带过期时间 (ex)
    - ttl 查询剩余时间
    - hget/hset 哈希操作
    - delete 删除
    - 每次修改操作后自动保存到文件
    """

    def __init__(self, file_path: str, auto_save: bool = True) -> None:  # noqa: FBT001, FBT002
        self.file_path = Path(file_path)
        self.auto_save = auto_save
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """从文件加载数据"""
        if self.file_path.exists() and self.file_path.stat().st_size > 0:
            with Path.open(self.file_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {}
            if self.auto_save:
                self._save()

    def _save(self) -> None:
        """保存数据到文件"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with Path.open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)

    def _clean_expired(self) -> bool:
        """清除所有过期的键，返回是否有删除操作"""
        now = time.time()
        to_delete = []
        for key, value in self.data.items():
            if isinstance(value, dict) and "_exp" in value and now >= value["_exp"]:
                to_delete.append(key)
        for key in to_delete:
            del self.data[key]
        return bool(to_delete)

    def get(self, key: str) -> Optional[Any]:
        """获取键的值，如果键不存在或已过期返回 None"""
        with self.lock:
            self._clean_expired()
            value = self.data.get(key)
            if isinstance(value, dict) and "_exp" in value:
                return value["_val"]
            return value

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> None:
        """设置键的值，可选过期时间（秒）"""
        with self.lock:
            if ex is not None:
                self.data[key] = {"_val": value, "_exp": time.time() + ex}
            else:
                self.data[key] = value
            if self.auto_save:
                self._save()

    def ttl(self, key: str) -> int:
        """获取键的剩余过期时间（秒）。
        返回:
            - 正数: 剩余秒数
            - -1: 键存在但没有过期时间
            - -2: 键不存在或已过期
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

    def hget(self, name: str, key: str) -> Optional[Any]:
        """获取哈希表中指定字段的值"""
        with self.lock:
            self._clean_expired()
            hash_map = self.data.get(name)
            if isinstance(hash_map, dict):
                return hash_map.get(key)
            return None

    def hset(self, name: str, key: str, value: Any) -> None:
        """设置哈希表中指定字段的值"""
        with self.lock:
            if name not in self.data or not isinstance(self.data.get(name), dict):
                self.data[name] = {}
            self.data[name][key] = value
            if self.auto_save:
                self._save()

    def exists(self, key: str) -> bool:
        """检查键是否存在且未过期"""
        with self.lock:
            self._clean_expired()
            return key in self.data

    def delete(self, key: str) -> None:
        """删除一个键"""
        with self.lock:
            if key in self.data:
                del self.data[key]
                if self.auto_save:
                    self._save()
