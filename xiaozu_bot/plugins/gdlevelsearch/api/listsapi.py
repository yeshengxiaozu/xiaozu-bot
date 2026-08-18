import json
from pathlib import Path
from ..paths import DATA_DIR

class Lists:
    DATA_PATHS = {
        "IDL": DATA_DIR/"idl.json",
        "HDL": DATA_DIR/"hdl.json",
        "MDL": DATA_DIR/"mdl.json",
        "EDL": DATA_DIR/"edl.json",
    }

    @staticmethod
    def _load_level_ids(
        name: str,
        path: str|Path,
    ) -> list[int]:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if name == "IDL":
            return [
                int(level["id"])
                for level in data["levels"]
            ]

        return [
            int(level["id"])
            for level in data
        ]

    @classmethod
    def _get_lists(cls) -> dict[str, list[int]]:
        return {
            name: cls._load_level_ids(name, path)
            for name, path in cls.DATA_PATHS.items()
        }

    @classmethod
    def search_level(cls, id_value: int | str):
        """
        在 IDL、HDL、MDL、EDL 四个列表中搜索 id。

        列表使用 0 作为占位符，因此索引本身就是排名：
        index 0 -> #0
        index 1 -> #1
        ...

        匹配成功返回形如 "IDL #20" 的字符串，
        否则返回 None。
        """
        try:
            target = int(id_value)
        except (TypeError, ValueError):
            return None

        if target == 0:
            return None

        lists = cls._get_lists()

        for name, level_list in lists.items():
            try:
                idx = level_list.index(target)
            except ValueError:
                continue
            return f"{name} #{idx+1}"

        return None
