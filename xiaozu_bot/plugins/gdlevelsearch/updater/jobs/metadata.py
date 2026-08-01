"""
从 Geometry Dash History API 获取关卡 online_id，
并利用本地 JSON 缓存避免重复请求。
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import requests
import urllib3
from nonebot import logger
from requests.adapters import HTTPAdapter, Retry


# 抄出来的ui函数
def demon_type(cache_filter_difficulty):
    return ["Unknown","Easy", "Medium","Hard", "Insane", "Extreme"][cache_filter_difficulty-7]

MANUAL_FALLBACK = False #控制是否进行人工干预

# ---------- 证书修复 ----------
try:
    import certifi
    CA_BUNDLE = certifi.where()
except ImportError:
    CA_BUNDLE = None
    logger.warning("certifi 未安装，将禁用 SSL 验证（不安全）")
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- 配置 ----------
os.environ['NO_PROXY'] = 'history.geometrydash.eu,geometrydash.eu'
GD_HISTORY_API = "https://history.geometrydash.eu/api/v1/search/level/advanced/"
CACHE_FILENAME = "metadata.json"
# 并发控制
MAX_CONCURRENT = 5          # 建议不超过 5，减少 API 压力
REQUEST_INTERVAL = 0.5      # 秒

# ---------- HTTP 会话（全局复用） ----------
http = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
http.mount("https://", adapter)

# 默认 verify 参数：优先使用 certifi，否则关闭验证（不推荐）
DEFAULT_VERIFY = CA_BUNDLE or False

# ---------- 辅助函数 ----------
def normalize_creator_name(name: str) -> str:
    """规范化作者名，与原 JS 逻辑保持一致"""
    return name.replace('&', 'and').strip().lower()

def get_cache_key(name: str, creator: str) -> tuple:
    """生成统一的缓存键（name, normalized_creator）"""
    clean_name = name.replace(" and more", "").strip()
    normalized_creator = normalize_creator_name(creator)
    return (clean_name, normalized_creator)

def save_metadata_cache(cache: list[dict], cache_dir: str):
    """保存 metadata 缓存到文件"""
    path = Path(cache_dir) / CACHE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=4)
    return []

def load_metadata_cache(cache_dir: Path):
    """读取 metadata 缓存到文件"""
    path = Path(cache_dir) / CACHE_FILENAME
    if path.exists():
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            logger.warning("Failed to load metadata cache, starting fresh")
    return []

# ---------- API 抓取 ----------
# 原来这里还有第三个参数 `loose: bool = False`，函数体里**从来没读过它**，
# 而唯一的调用方（下面 enrich 那段）却一直传 True 进来，也就是说
# 「松匹配」这个开关接上了但根本没实现，代码在说谎。
# 调用方本来就有兜底（拿不到 online_id 就转去问 gdapi），所以直接把这个
# 参数删掉，行为不变，只是不再假装支持一个不存在的功能。
def fetch_level_data(name: str, creator: str) -> dict | None:
    name = name.replace(" and more","")
    params = {
        "query": name,
        "limit": 100,
        "filter": "cache_stars = 10",
    }

    try:
        resp = http.get(GD_HISTORY_API, params=params, timeout=10, verify=DEFAULT_VERIFY)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout as e:
        # 原来这里没写 as e，日志却引用了 e —— 真超时的时候会在 except 里
        # 再抛一个 NameError，把超时伪装成别的错
        logger.error(f"Request timed out for '{name}' by '{creator}': {e}")
        return None
    except requests.exceptions.HTTPError as e:
        status_code = getattr(e.response, 'status_code', 0) if hasattr(e, 'response') else 0
        logger.error(f"HTTP error {status_code} for '{name}' by '{creator}': {e}")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection failed for '{name}' by '{creator}': {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON response for '{name}' by '{creator}': {e}")
        return None
    except Exception as e:
        logger.error(f"API request failed for '{name}' by '{creator}': {type(e).__name__}: {e}")
        return None

    hits = data.get("hits", [])

    # 只有一个结果直接返回
    if len(hits) == 1:
        return hits[0]

    # 尝试按作者名匹配
    creator_lower = creator.lower()
    exact_creator_matches = [
        h for h in hits
        if creator_lower in (h.get("cache_username", "") or "").lower()
        or (h.get("cache_username", "") or "").lower() in creator_lower
    ]
    if len(exact_creator_matches) == 1:
        return exact_creator_matches[0]

    # 如果没有结果
    if len(hits) == 0:
        if not MANUAL_FALLBACK:
            logger.info(f"gdhistory: No results found for '{name}' by '{creator}'")
            return None
        try:
            manual_id = str(input(f"finding unmatched level, please manually enter id for {name} by {creator}").strip())
            return {"online_id": int(manual_id)}
        except ValueError:
            logger.error("Invalid ID format entered")
            return None

    # 都不行就按 demon 难度从高到低排序，取最高难度的
    # hits.sort(key=lambda h: h.get("cache_filter_difficulty", 0), reverse=True)
    # 尝试手动选择
    if len(hits) == 1:
        return hits[0]

    # 过滤出名称匹配的关卡
    name_normalized = name.strip()
    hits = [level for level in hits if level.get('cache_level_name', '').strip() == name_normalized]

    if len(hits) == 1:
        return hits[0]

    if not MANUAL_FALLBACK:
        logger.info("Multiple levels found, manual selection required")
        return None

    if len(hits) == 0:
        try:
            manual_id = int(input(f"finding unmatched level, please manually enter id for {name} by {creator}").strip())
            return {"online_id": manual_id}
        except ValueError:
            logger.error("Invalid ID format entered")
            return None

    logger.info("Multiple levels found, please select one:")
    logger.info(f"Spreadsheet info: {name} by {creator}\n    results:")
    for level in hits:
        logger.info(f"{level['cache_level_name']} by {level['cache_username']} ({demon_type(level['cache_filter_difficulty'])} Demon)")
    return hits[int(input())]

# ---------- 并发队列控制 ----------
class RateLimiter:
    """简单的间隔控速器"""
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._last = 0.0
        self._lock = Lock()

    def wait(self):
        with self._lock:
            elapsed = time.time() - self._last
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self._last = time.time()

# ---------- 主功能：为关卡列表填充 ID ----------
def enrich_levels_with_ids(
    levels: list[dict],
    cache_dir: Path,
    max_workers: int = MAX_CONCURRENT,
    interval: float = REQUEST_INTERVAL,
) -> None:
    """
    为 levels 列表中的每个关卡添加 'id' 字段（online_id）。
    该操作直接修改传入的 levels 对象。

    levels: 列表，每个元素应包含 'name' 和 'creator' 键。
    cache_dir: 缓存目录，会在此目录下读写 metadata.json。
    """
    # 加载现有缓存
    cache = load_metadata_cache(cache_dir)
    # 建立快速查找： (name, normalized_creator) -> id
    cache_map = {}
    for entry in cache:
        key = (entry["name"], entry["creator"])
        cache_map[key] = int(entry["id"])

    # 找出需要抓取的关卡
    to_fetch = []
    for level in levels:
        name = level["name"]
        creator_norm = normalize_creator_name(level["creator"])
        key = (name, creator_norm)
        if key in cache_map:
            level["id"] = cache_map[key]
        else:
            to_fetch.append((level, name, level["creator"], creator_norm))

    if not to_fetch:
        logger.info("All levels already have metadata cached.")
        return

    logger.info(f"Fetching metadata for {len(to_fetch)} levels...")

    rate_limiter = RateLimiter(interval)
    lock = Lock()
    new_entries = []

    def fetch_one(level_obj, name, creator_raw, creator_norm):
        # 查询前再次检查缓存（防止并发重复插入）
        with lock:
            if (name, creator_norm) in cache_map:
                level_obj["id"] = cache_map[(name, creator_norm)]
                return

        rate_limiter.wait()

        try:
            data = fetch_level_data(name, creator_raw)

            if data is None or data.get("online_id", None) is None:
                #尝试使用gdapi
                # 用插件主目录那份 gdapi。updater/jobs 下原来还有一份 571 行的
                # 副本，已经和主的分叉了（主的重构成了 parse_server_key_value_pairs
                # 那套并多了 GDUser），留着只会让 bug 要修两遍，所以删掉了。
                try:
                    from ...api.gdapi import search_levels_by_name  # bot 里跑
                except ImportError:
                    from gdapi import search_levels_by_name  # 单独跑脚本时
                levels = search_levels_by_name(name)
                if not levels:
                    logger.error(f"Could not find metadata for '{name}' by '{creator_raw}'")
                    return
                levels = [level for level in levels if level.stars == 10 and level.level_name.strip().lower() == name.strip().lower()]
                if len(levels) == 0:
                    logger.error(f"Could not find metadata for '{name}' by '{creator_raw}'")
                    return
                if len(levels) > 1:
                    logger.error(f"Multiple levels found, manual selection required for '{name}' by '{creator_raw}'")
                    return
                logger.info(f"fallback to gdapi for '{name}' by '{creator_raw}' successfully")
                online_id = levels[0].level_id
            else:
                online_id = data.get("online_id", None)

            level_obj["id"] = int(online_id)
            entry = {"name": name, "creator": creator_norm, "id": online_id}

            with lock:
                cache_map[(name, creator_norm)] = online_id
                new_entries.append(entry)

        except Exception as e:
            logger.error(f"Error fetching metadata for '{name}' by '{creator_raw}': {e}")

    if MANUAL_FALLBACK:
        for level, name, creator_raw, creator_norm in to_fetch:
            fetch_one(level,name,creator_raw, creator_norm)
    else:
        # 使用线程池并发
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(fetch_one, level, name, creator_raw, creator_norm)
                for level, name, creator_raw, creator_norm in to_fetch
            ]
            # 等待所有任务完成
            for future in as_completed(futures):
                future.result()  # 抛出异常（如果有）

    # 保存缓存
    cache.extend(new_entries)
    save_metadata_cache(cache, cache_dir)
    logger.info(f"Metadata fetch complete. {len(new_entries)} new entries added to cache.")
