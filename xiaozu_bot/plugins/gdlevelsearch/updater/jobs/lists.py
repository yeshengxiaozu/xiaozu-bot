from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from xiaozu_bot.utils.json_storage import write_json_atomic

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    )
}


MAX_WORKERS = 16


LISTS = {
    "hard": {
        "base_url": "https://hdl.pages.dev/data",
        "output": staged("hdl.json"),
        "use_name_map": True,
    },
    "medium": {
        "base_url": "https://mediumdemonslist.pages.dev/data",
        "output": staged("mdl.json"),
        "use_name_map": False,
    },
    "easy": {
        "base_url": "https://easydemonlist.pages.dev/data",
        "output": staged("edl.json"),
        "use_name_map": False,
    },
}


def _create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _fetch_level(
    base_url: str,
    level_name: str,
    position: int,
    name_map: dict[str, str] | None,
) -> dict[str, str]:
    """获取单个关卡 JSON。"""

    session = _create_session()

    response = session.get(
        f"{base_url}/{level_name}.json",
        timeout=20,
    )
    response.raise_for_status()

    data = response.json()

    if name_map is not None:
        author_id = data["author"]

        creator = name_map.get(
            str(author_id),
            str(author_id),
        )
    else:
        creator = str(data["author"])

    return {
        "id": str(data["id"]),
        "position": str(position),
        "name": str(data["name"]),
        "creator": creator,
    }


def _fetch_list(
    base_url: str,
    use_name_map: bool,
) -> list[dict[str, str]]:
    """
    获取一个 Demon List 的前 150 个关卡。

    _list.json 和 _name_map.json（如果需要）顺序请求。
    只有具体关卡 JSON 使用并发。
    """

    session = _create_session()

    response = session.get(
        f"{base_url}/_list.json",
        timeout=20,
    )
    response.raise_for_status()

    level_names: list[str] = response.json()
    level_names = level_names[:150]

    name_map: dict[str, str] | None = None

    if use_name_map:
        response = session.get(
            f"{base_url}/_name_map.json",
            timeout=20,
        )
        response.raise_for_status()

        raw_name_map = response.json()

        name_map = {
            str(author_id): str(name)
            for author_id, name in raw_name_map.items()
        }

    levels: dict[int, dict[str, str]] = {}

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS,
    ) as executor:

        futures = {
            executor.submit(
                _fetch_level,
                base_url,
                level_name,
                position,
                name_map,
            ): (position, level_name)
            for position, level_name in enumerate(
                level_names,
                start=1,
            )
        }

        for future in as_completed(futures):
            position, level_name = futures[future]

            try:
                level = future.result()
            except Exception as e:
                raise RuntimeError(
                    f"第 {position} 名关卡 "
                    f"{level_name!r} 获取失败"
                ) from e

            levels[position] = level

    return [
        levels[position]
        for position in range(
            1,
            len(level_names) + 1,
        )
    ]


def _fetch_one(
    list_name: str,
    config: dict,
) -> tuple[str, list[dict[str, str]]]:
    """获取一个完整的 Demon List。"""

    levels = _fetch_list(
        config["base_url"],
        config["use_name_map"],
    )

    return list_name, levels


def fetch() -> None:
    """
    获取 Hard / Medium / Easy Demon List。

    三个 List 可以并发。

    每个 List：
    - _list.json：顺序请求
    - _name_map.json：仅 HDL 顺序请求
    - 具体关卡 JSON：并发请求
    """

    with ThreadPoolExecutor(
        max_workers=len(LISTS),
    ) as executor:

        futures = {
            executor.submit(
                _fetch_one,
                list_name,
                config,
            ): (list_name, config)
            for list_name, config in LISTS.items()
        }

        for future in as_completed(futures):
            list_name, config = futures[future]

            try:
                _, levels = future.result()
            except Exception:
                raise

            write_json_atomic(
                config["output"],
                levels,
                ensure_ascii=False,
                indent=4,
            )


if __name__ == "__main__":
    fetch()