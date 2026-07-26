# updater/notify.py

import traceback
from nonebot import get_bot, logger
from typing import Optional

ADMIN_ID = 3251605531  # 改成你的 QQ

_last_error_key = None


def _error_key(err: Exception) -> str:
    return f"{type(err).__name__}:{str(err)}"


async def report_error(title: str, err: Exception, context: Optional[dict] = None):
    """
    统一错误上报函数
    """

    if context is None:
        context = {}
    global _last_error_key

    # ---- 防重复刷屏 ----
    # 放在拿 bot 之前：拿不到 bot 的时候也要照样更新这个键，
    # 否则「没连上 bot 的那几次」不算数，等真连上了又会把老错误重报一遍。
    key = _error_key(err)
    if key == _last_error_key:
        logger.warning("[NOTIFY] duplicate error ignored")
        return
    _last_error_key = key

    # ---- traceback ----
    tb = traceback.format_exc()

    # ---- context ----
    context_str = ""
    if context:
        context_str = "\n".join([f"{k}: {v}" for k, v in context.items()])

    msg = f"""
🚨 数据更新异常

标题: {title}

类型: {type(err).__name__}
错误: {str(err)}

上下文:
{context_str if context_str else "None"}

--- traceback ---
{tb[-1200:]}
"""

    # get_bot() 必须也在 try 里面。
    #
    # 这个函数是从 daily_update_job 的 except 分支里调的，也就是说调用它的时候
    # **已经有一个真正的错误在手上了**。而 get_bot() 在没有任何 bot 连着的时候
    # 抛 ValueError("There are no bots to get.")。以前它写在 try 外面，于是：
    # 定时任务在没连 QQ 的时候跑失败 -> 想上报 -> get_bot 抛 ValueError ->
    # 这个 ValueError 从 except 分支里冒出去，把原始错误顶掉，
    # apscheduler 日志里只剩一句莫名其妙的 "There are no bots to get."，
    # 真正挂在哪一步反而看不见了。
    #
    # 现在拿不到 bot 就把整份报告打进日志，函数正常返回 —— 上报失败不该
    # 变成第二个异常，更不该盖掉第一个。
    try:
        bot = get_bot()
    except Exception:
        logger.error(f"[NOTIFY] 没有可用的 bot，错误报告只能写进日志：\n{msg}")
        return

    try:
        await bot.send_private_msg(
            user_id=ADMIN_ID,
            message=msg
        )

        logger.info("[NOTIFY] error sent to admin")

    except Exception:
        # ⚠️ 防止"连通知系统也炸"
        logger.exception(f"[NOTIFY] 发送失败，错误报告只能写进日志：\n{msg}")