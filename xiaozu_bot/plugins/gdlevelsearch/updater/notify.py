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

    bot = get_bot()

    # ---- 防重复刷屏 ----
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

    try:
        await bot.send_private_msg(
            user_id=ADMIN_ID,
            message=msg
        )

        logger.info("[NOTIFY] error sent to admin")

    except Exception as send_err:
        # ⚠️ 防止“连通知系统也炸”
        logger.exception("[NOTIFY] failed to send error message")