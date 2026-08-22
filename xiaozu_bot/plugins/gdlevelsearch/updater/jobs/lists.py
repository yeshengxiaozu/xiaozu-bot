from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from xiaozu_bot.utils.json_storage import write_json_atomic

from ...api.http import RequestSession
from ...constants import USER_AGENT

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged


HEADERS = {"User-Agent": USER_AGENT}
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


def _create_session() -> RequestSession:
    return RequestSession()


def _fetch_level(
    base_url: str,
    level_name: str,
    position: int,
    name_map: dict[str, str] | None,
    session: RequestSession | None = None,
) -> dict[str, str]:
    """Fetch one level JSON using the caller's shared connection pool."""
    owned_session = session is None
    session = session or _create_session()
    try:
        response = session.get(f"{base_url}/{level_name}.json", timeout=20)
        response.raise_for_status()
        data = response.json()

        author_id = data["author"]
        creator = (
            name_map.get(str(author_id), str(author_id))
            if name_map is not None
            else str(author_id)
        )
        return {
            "id": str(data["id"]),
            "position": str(position),
            "name": str(data["name"]),
            "creator": creator,
        }
    finally:
        if owned_session:
            session.close()


def _fetch_list(base_url: str, use_name_map: bool) -> list[dict[str, str]]:
    """Fetch one Demon List with one shared client per list."""
    session = _create_session()
    try:
        response = session.get(f"{base_url}/_list.json", timeout=20)
        response.raise_for_status()
        level_names: list[str] = response.json()[:150]

        name_map: dict[str, str] | None = None
        if use_name_map:
            response = session.get(f"{base_url}/_name_map.json", timeout=20)
            response.raise_for_status()
            name_map = {
                str(author_id): str(name)
                for author_id, name in response.json().items()
            }

        levels: dict[int, dict[str, str]] = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(
                    _fetch_level,
                    base_url,
                    level_name,
                    position,
                    name_map,
                    session,
                ): (position, level_name)
                for position, level_name in enumerate(level_names, start=1)
            }
            for future in as_completed(futures):
                position, level_name = futures[future]
                try:
                    levels[position] = future.result()
                except Exception as e:
                    raise RuntimeError(
                        f"绗?{position} 鍚嶅叧鍗?{level_name!r} 鑾峰彇澶辫触"
                    ) from e

        return [levels[position] for position in range(1, len(level_names) + 1)]
    finally:
        session.close()


def _fetch_one(
    list_name: str,
    config: dict,
) -> tuple[str, list[dict[str, str]]]:
    return list_name, _fetch_list(config["base_url"], config["use_name_map"])


def fetch() -> None:
    """Fetch Hard / Medium / Easy Demon Lists concurrently."""
    with ThreadPoolExecutor(max_workers=len(LISTS)) as executor:
        futures = {
            executor.submit(_fetch_one, list_name, config): (list_name, config)
            for list_name, config in LISTS.items()
        }
        for future in as_completed(futures):
            _list_name, config = futures[future]
            _, levels = future.result()
            write_json_atomic(
                config["output"],
                levels,
                ensure_ascii=False,
                indent=4,
            )


if __name__ == "__main__":
    fetch()
