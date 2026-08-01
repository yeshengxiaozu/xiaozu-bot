"""*gdsearchhelp 与 *gdsearch_update。"""

import asyncio

from nonebot import on_command
from nonebot.internal.adapter import Event
from nonebot.permission import SUPERUSER

from ..updater.runner import run_all_async

gdsearchhelp = on_command("gdsearchhelp")


@gdsearchhelp.handle()
async def handle_gdsearchhelp() -> None:
    HELP_STR = """使用*gdsearch 关卡名或id 以搜索关卡
数据来源包括GDDL NLW等chart AREDL
以及Plat difficulty chart等plat chart
可以使用*references (gddl/nlw/plat)查询对应的参考线

*gdsearch 只查本地收录的榜单，所以基本只有demon
想搜服务器上的任意关卡用*gdfullsearch，它直接问GD服务器要数据：
  *gdfullsearch 关卡名         默认只搜rated
  *gdfullsearch 关卡名 -a      连没评级的一起搜
  *gdfullsearch 关卡名 -d      只搜demon，后面可以跟1-5或easy/medium/hard/insane/extreme
  *gdfullsearch 关卡名 -u 难度  只搜非demon，0-5或auto/easy/normal/hard/harder/insane（0是auto）
结果多的时候会分页，输入序号选中，n下一页，p上一页，结束取消

*gdratings 关卡名或id 看这关在GDDL上每个人给的tier和enjoyment
  -s 排序   tier / enj / date / progress / attempts / rr
  -asc      正序（默认倒序）
  -v        只看通关的人

管理员可用 *gdsearch_manage help 查看手动管理命令帮助
"""  # noqa: N806
    # 那几个references的实现我扔给xiaozubot_help模块了
    await gdsearchhelp.finish(HELP_STR)


update_cmd = on_command("gdsearch_update", permission=SUPERUSER, priority=1, block=True)


@update_cmd.handle()
async def _handle(event: Event):
    from .. import reload_all  # 延迟导入，避免和插件包装配互相依赖

    await update_cmd.send("🚀 开始执行手动更新...")
    try:
        result = await run_all_async()
    except Exception as e:
        await update_cmd.finish(f"❌ 更新失败\n{e}")
    else:
        # 抓完立刻重载，这样不用重启就能查到新数据
        await asyncio.to_thread(reload_all)
        await update_cmd.finish(f"✅ 更新完成，缓存已重载\n{result}")
