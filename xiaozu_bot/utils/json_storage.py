import fnmatch
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from nonebot import logger


def plugin_storage(plugin_file: str | Path, name: str = "storage.json") -> Path:
    """算出某个插件的存储文件路径，相对插件自己的位置。

    调用方传 __file__ 就行：
        r = JsonRedis(plugin_storage(__file__))

    以前各处写的是 "xiaozu_bot/plugins/xxx/data/storage.json" 这种相对
    当前工作目录的路径，必须从仓库根目录启动 bot，换个地方启动就会在
    错误的位置新建一个空存储 —— 数据看着就像丢了。
    """
    return Path(plugin_file).resolve().parent / "data" / name


class JsonRedis:
    """基于 JSON 文件的键值存储，模拟 Redis 的常用 API。

    支持:
    - get/set 带过期时间 (ex)
    - ttl 查询剩余时间
    - hget/hset/hkeys/hexists 哈希操作
    - keys 按 glob 匹配键名
    - delete 删除
    - 每次修改操作后自动保存到文件

    和真 redis 的一个区别：真 redis 开了 decode_responses=True 之后
    存什么读出来都是 str，这里是存什么类型读出来还是什么类型。
    现有调用方要么存的就是 str，要么读出来立刻 int()，所以没影响；
    新写调用的时候留意一下别拿 int 去和字符串比。
    """

    def __init__(self, file_path: str | Path, auto_save: bool = True) -> None:
        self.file_path = Path(file_path)
        self.auto_save = auto_save
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """从文件加载数据。

        文件坏了不抛异常 —— 这个类是在插件 import 期构造的，
        一个坏的 storage.json 会让整个插件加载不了。
        坏文件会被改名留档，然后从空的开始。
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
        """保存数据到文件。

        先写同目录下的临时文件再 os.replace 换过去 —— 同一个文件系统上
        replace 是原子的。以前是直接覆盖写，写到一半进程被 kill
        就留下一个半截的 json，下次启动直接读不了。
        """
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
        """清除所有过期的键，返回是否有删除操作"""
        now = time.time()
        to_delete = []
        for key, value in self.data.items():
            if isinstance(value, dict) and "_exp" in value and now >= value["_exp"]:
                to_delete.append(key)
        for key in to_delete:
            del self.data[key]
        return bool(to_delete)

    def get(self, key: str) -> Any | None:
        """获取键的值，如果键不存在或已过期返回 None"""
        with self.lock:
            self._clean_expired()
            value = self.data.get(key)
            if isinstance(value, dict) and "_exp" in value:
                return value["_val"]
            return value

    def set(self, key: str, value: Any, ex: int | None = None) -> None:
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

    def hget(self, name: str, key: str) -> Any | None:
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

    def _as_hash(self, name: str) -> dict[str, Any]:
        """把 name 当哈希表取出来，取不到就返回空 dict。

        带过期时间的普通键存的也是 dict（{_val, _exp}），
        那种不算哈希表，要排掉。
        """
        value = self.data.get(name)
        if isinstance(value, dict) and "_exp" not in value:
            return value
        return {}

    def hkeys(self, name: str) -> list[str]:
        """列出哈希表里所有的字段名，表不存在就返回空列表"""
        with self.lock:
            self._clean_expired()
            return list(self._as_hash(name).keys())

    def hexists(self, name: str, key: str) -> bool:
        """哈希表里有没有这个字段"""
        with self.lock:
            self._clean_expired()
            return key in self._as_hash(name)

    def keys(self, pattern: str = "*") -> list[str]:
        """按 glob 匹配键名，语义对齐 redis 的 KEYS。

        调用方有用关键字传的（r.keys(pattern="roulette_status*")），
        所以参数名必须叫 pattern。
        """
        with self.lock:
            self._clean_expired()
            return fnmatch.filter(list(self.data.keys()), pattern)

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
