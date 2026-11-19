"""*gdsearchhelp, *gdsearch_update, and *gdsearch_store_update."""

import asyncio

from nonebot import logger, on_command
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

*aredlrank 排名 直接出AREDL上该排名的关卡（1到当前最大排名）

管理员可用 *gdsearch_manage help 查看手动管理命令帮助
"""  # noqa: N806
    # 那几个references的实现我扔给xiaozubot_help模块了
    await gdsearchhelp.finish(HELP_STR)


update_cmd = on_command("gdsearch_update", permission=SUPERUSER, priority=1, block=True)
gdsearch_store_update = on_command(
    "gdsearch_store_update", permission=SUPERUSER, priority=1, block=True
)


def _format_partial(result: dict, failed: list, skipped: list) -> str:
    lines = [
        f"部分更新完成：成功 {len(result.get('success') or [])}、"
        f"失败 {len(failed)}、跳过 {len(skipped)}，"
        f"发布 {len(result.get('published') or [])} 个文件",
        "失败分支保留旧快照；成功分支已发布并重载。",
    ]
    lines.extend(
        f"失败 job：{entry.get('job')} [{entry.get('type')}] {entry.get('error')}"
        for entry in failed
    )
    lines.extend(
        f"跳过 job：{entry.get('job')}（{entry.get('reason')}："
        f"{', '.join(entry.get('missing') or [])}）"
        for entry in skipped
    )
    return "\n".join(lines)


@update_cmd.handle()
async def _handle(event: Event):
    from .. import reload_all  # 延迟导入，避免和插件包装配互相依赖

    await update_cmd.send("🚀 开始执行手动更新...")
    try:
        result = await run_all_async()
    except Exception as e:
        # A crash after publishing still leaves fresh files on disk.
        try:
            await asyncio.to_thread(reload_all)
        except Exception:
            logger.exception("[gdsearch_update] 更新失败后重载也失败")
        await update_cmd.finish(f"❌ 更新失败\n{e}")
        return

    # Even a partial failure must reload: successful branches are live now,
    # and aredlapi.reload() is one of the steps inside reload_all().
    await asyncio.to_thread(reload_all)
    failed = result.get("failed") or []
    skipped = result.get("skipped") or []
    if failed or skipped:
        await update_cmd.finish(_format_partial(result, failed, skipped))
    await update_cmd.finish(f"✅ 更新完成，缓存已重载\n{result}")


@gdsearch_store_update.handle()
async def _handle_store_update() -> None:
    from ..updater import gddl_store_update_job

    await gdsearch_store_update.send("🚀 开始更新 GDDL store...")
    if await gddl_store_update_job():
        await gdsearch_store_update.finish("✅ GDDL store 更新完成，缓存已重载")
    await gdsearch_store_update.finish("❌ GDDL store 更新失败，或已有更新正在进行")
