from __future__ import annotations

import re
import time

import requests

from xiaozu_bot.utils.json_storage import write_json_atomic

try:
    from ..paths import staged
except ImportError:
    from updater.paths import staged


MAIN_URL = "https://insanedemonlist.com/main"
EXTENDED_URL = "https://insanedemonlist.com/extended"

OUTPUT_PATH = staged("idl.json")


def fetch() -> None:
    """
    获取 Insane Demon List 的 Main + Extended，
    解析完整 150 个条目并保存为 JSON。

    JSON 中每个条目仅包含：
        position
        name
        creator
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        )
    }

    levels: list[dict[str, str]] = []

    for url in (MAIN_URL, EXTENDED_URL):
        response = requests.get(
            url,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()

        text = response.text

        # Next.js response 中的结构：
        #
        # \"level\":{\"id\":\"...\",
        # \"formerRank\":null,
        # \"name\":\"in canon\",
        # \"position\":1,
        # \"publisher\":\"cordeaux\",...
        #
        # publisher 对应网页显示的 creator。
        pattern = re.compile(
            r'\\"level\\":\{'
            r'.*?'
            r'\\"name\\":\\"(?P<name>.*?)\\"'
            r'.*?'
            r'\\"position\\":(?P<position>\d+)'
            r'.*?'
            r'\\"publisher\\":\\"(?P<creator>.*?)\\"'
            r'.*?'
            r'\}',
            re.DOTALL,
        )

        levels.extend(
            {
                "position": match.group("position"),
                "name": match.group("name").strip(),
                "creator": match.group("creator").strip().lower(),
            }
            for match in pattern.finditer(text)
        )
    # 按排名重新排序
    levels.sort(key=lambda x: int(x["position"]))

    # 防止网页结构发生变化导致静默生成残缺数据
    if len(levels) != 150:
        raise RuntimeError(
            f"解析失败：预期 150 个条目，实际获取 {len(levels)} 个。"
        )

    output={
        "timestamp":time.time(),
        "levels":levels
    } # 统一格式方便后续统一处理

    write_json_atomic(
            OUTPUT_PATH,
            output,
            ensure_ascii=False,
            indent=4,
        )


if __name__ == "__main__":
    fetch()
