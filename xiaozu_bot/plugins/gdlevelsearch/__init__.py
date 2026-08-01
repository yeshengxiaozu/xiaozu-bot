"""gdlevelsearch：Geometry Dash 关卡检索插件。

目录结构：
- api/       数据源客户端（GD 服务器、GDDL、AREDL、NLW、Plat chart）
- render/    出图与本地图标渲染（draw / icons / iconrender）
- services/  跨命令共享的搜索业务（search_by_name 等）
- commands/  各命令的 matcher + handler（import 本包即完成注册）
- updater/   定时数据更新
- paths.py / constants.py  公用路径与常量
"""

import asyncio

from nonebot import get_driver, logger, require

from .api import aredlapi, gdapi, gddlapi, nlwapi, platapi  # noqa: F401
from .render import draw, iconrender, icons  # noqa: F401

require("nonebot_plugin_apscheduler")


def reload_all() -> None:
    """把磁盘上的缓存重新读进内存。

    各个 api 模块都是在 import 的时候把数据读进模块级 list/dict 的，
    不主动调用这个的话，updater 半夜抓完的新数据要等到下次重启才生效。
    """
    logger.info("[gdlevelsearch] 开始重载本地缓存")
    for name, module in (("nlw", nlwapi), ("plat", platapi), ("aredl", aredlapi)):
        try:
            module.reload()
        except Exception:
            # 单个源挂了不影响别的，内存里保留旧数据总比清空好
            logger.exception(f"[gdlevelsearch] {name} 重载失败，保留原有数据")
    logger.info("[gdlevelsearch] 缓存重载完毕")


@get_driver().on_startup
async def _refresh_aredl_on_startup() -> None:
    """启动之后在后台把 AREDL 刷一遍。"""
    try:
        await asyncio.to_thread(aredlapi.reload)
    except Exception:
        logger.exception("[gdlevelsearch] 启动后刷新 AREDL 失败，继续用缓存")


# 关键：只 import updater，让 scheduler 注册生效。
# 必须放在 reload_all 定义之后 —— updater 的定时任务要回头调它。
from . import updater  # noqa: E402, F401, RUF100

# ---------------------------------------------------------------------------
# 命令模块：import 即注册 matcher。下面这些名字同时是包级 re-export，
# 保持 `gdlevelsearch.gdsearch` 这类旧引用（含测试）继续可用。
# ---------------------------------------------------------------------------
from .commands.dailydemon import dailydemon  # noqa: F401
from .commands.fullsearch import (  # noqa: F401
    _drop_fullsearch,
    fullsearch_sessions,
    fullsearch_timeouts,
    gdfullsearch,
    gdfullsearchselect,
    has_fullsearch,
)
from .commands.gdicon import gdicon  # noqa: F401
from .commands.gduser import gduser  # noqa: F401
from .commands.help import gdsearchhelp, update_cmd  # noqa: F401
from .commands.random import gdrandom  # noqa: F401
from .commands.ratings import (  # noqa: F401
    _drop_ratings,
    gdratings,
    gdratingsselect,
    has_ratings,
    ratings_sessions,
    ratings_timeouts,
)
from .commands.search import (  # noqa: F401
    clear_search_cache,
    gdsearch,
    gdsearchselect,
    has_cache,
    search_cache,
    timeout_tasks,
)
from .services.search import (  # noqa: F401
    SearchResult,
    _add_search_result,
    _clear_all_sessions,
    get_creator,
    get_difficulty,
    getlevelinfo,
    search_by_name,
    send_result,
)
