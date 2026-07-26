from nonebot import get_plugin_config, on_command
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="help",
    description="",
    usage="",
    config=Config,
)

config = get_plugin_config(Config)

xiaozubothelp = on_command("help")
references = on_command("references")

# 命令按用途分组。加新命令的时候记得回来补一句，别再让 help 落后于实际功能。
# key 是用户输入的分类名，值是 (标题, 正文)
HELP_SECTIONS: dict[str, tuple[str, str]] = {
    "gd": (
        "GD 关卡查询",
        """*gdsearch 关卡名或id
  查本地收录的榜单（GDDL / NLW / IDS / LW / HDS / plat chart），
  结果出一张图。收录的基本都是 demon，普通图搜不到就用下面那个。
  搜到多个同名的会列出来，输入序号选。

*gdfullsearch 关键词 [-a] [-d [难度]] [-u 难度]
  直接问 GD 服务器要数据，服务器上有的关卡都能搜到。
  默认只搜 rated；-a 连没评级的一起搜。
  -d 只搜 demon，后面可跟 1-5 或 easy/medium/hard/insane/extreme
  -u 只搜非 demon，0-5 或 auto/easy/normal/hard/harder/insane（0 是 auto）
  结果分页：输入序号选中 / n 下一页 / p 上一页 / 结束 取消

*gdratings 关卡名或id [-s 排序] [-asc] [-v]
  看这关在 GDDL 上每个人提交的 tier 和 enjoyment。
  -s 可选 tier / enj / date / progress / attempts / rr
  -asc 正序（默认倒序），-v 只看通关的人
  同样支持 n / p 翻页

*gduser 用户名        查 GD 玩家的星星、月亮、demon 数等
*gd随机推关 低 [高]    在指定 GDDL tier 区间里随机推一关
*references 表名 [页] 各难度表的参考线，表名：gddl / nlw / lw / ids / hds / plat
*gdsearchhelp         gd 相关命令的简要说明""",
    ),
    "guess": (
        "猜图",
        """*guess_start        出一道猜图题
*guess_start_hard   同上，但截图更小
*guess_start_ultra  同上，再小一号
*guess 答案         回答当前题目
*guess_giveup       放弃当前题目，公布答案
*guess_count        看全服累计猜了多少次、对了多少

小小卒给你的消息按了按钮 = 答错了
按问号 = 现在没有题（一般是已经被人答掉了）""",
    ),
    "fun": (
        "娱乐杂项",
        """*jrrp            今日人品，一天一次
*zhua            随机抓一只小卒
*show 名称       指定名字看某只小卒
*random 选项...  在你给的选项里随机挑一个
*map             随机给一张地图
*jwz 内容 [时长]  我能在患有健忘症的情况下患有健忘症吗？
*today 词1 词2 词3  今天是著名(词1)大神(词2)(词3)的日子……
*game 编号       猜数字之类的小游戏建议，编号 1~4
*ultra           不要再！
*nsdd            你说的对，但是……
*insult          让小小卒骂你一句
*news（*公告 / *新闻）  更新公告
戳一戳小小卒也会有反应""",
    ),
    "ai": (
        "AI 与语音",
        """*ai 内容       和小小卒聊天（本地模型，别期待太高）
*say 内容      让小小卒把这句话读出来
*say_i 要求 内容  同上，但可以额外描述语气/风格

语音功能依赖本地 TTS，不一定一直开着。""",
    ),
    "demon": (
        "恶魔轮盘（仅限特定群）",
        """*betgame       加入一局
*setmode 编号  设置模式：0 普通 / 1 身份 / 2 膀胱
*开枪（*射击）  轮到你的时候开枪
*恶魔道具      查看自己的道具
*使用 道具（*使用道具）  用一个道具
*查看局势      看当前场上情况
*恶魔投降      认输
.恶魔帮助（。恶魔帮助）  规则详细说明，注意是点开头不是星号

这套只在几个指定的群里能用。""",
    ),
}

ADMIN_SECTION = """*gdsearch_update  手动跑一遍 gd 数据更新，跑完自动重载缓存
*guess_cheat      看当前题目答案
*guess_rc         清掉出题冷却
以上都只有超级用户能用。

（蓝莓/轮盘那套已经下线了，相关命令不再列出）"""

SECTION_ALIASES = {
    "gd": "gd", "关卡": "gd", "搜索": "gd", "search": "gd",
    "guess": "guess", "猜图": "guess", "猜": "guess",
    "fun": "fun", "娱乐": "fun", "杂项": "fun",
    "ai": "ai", "say": "ai", "语音": "ai",
    "demon": "demon", "恶魔": "demon", "game": "demon",
    "admin": "admin", "管理": "admin",
}


def _overview() -> str:
    lines = [
        "欢迎使用小小卒！所有命令以 * 开头",
        "用户群 1035708051，功能建议可以在里面提",
        "",
        "*help 分类 看某一类的详细用法：",
    ]
    lines += [f"  *help {key:<7} {title}" for key, (title, _) in HELP_SECTIONS.items()]
    lines += [
        "  *help admin   管理命令（超级用户）",
        "",
        "最常用的几个：",
        "  *gdsearch 关卡名     查关卡（本地榜单，基本都是 demon）",
        "  *gdfullsearch 关键词  查关卡（直连 GD 服务器，什么都能搜）",
        "  *gdratings 关卡名    看 GDDL 上大家给的 tier / enjoyment",
        "  *guess_start         来一道猜图",
        "  *jrrp                今日人品",
    ]
    return "\n".join(lines)


@xiaozubothelp.handle()
async def handle_help(arg: Message = CommandArg()) -> None:
    name = arg.extract_plain_text().strip().lower()
    if not name:
        await xiaozubothelp.finish(_overview())

    key = SECTION_ALIASES.get(name)
    if key == "admin":
        await xiaozubothelp.finish("【管理命令】\n" + ADMIN_SECTION)
    if key is None:
        await xiaozubothelp.finish(
            f"没有「{name}」这个分类。可以用的分类："
            + " / ".join(HELP_SECTIONS.keys())
            + " / admin"
        )

    title, body = HELP_SECTIONS[key]
    await xiaozubothelp.finish(f"【{title}】\n{body}")

REF_GDDL = [
"""Tier 1: The Nightmare, THE LIGHTNING ROAD, Shiver, STARPUNK, iS
Tier 2: Crazy Bolt, Speed Racer, Speed of Light, Slap Squad II, Born Survivor
Tier 3: Clubstep, demon park, X, Spark, Buried Angel
Tier 4: Chaoz Impact, Death Moon , Motion, Running in the 90s, Mass Production
Tier 5: Deadlocked, Sidestep, Collapse, mmrr, HEMI""",
"""Tier 6: Solar Circles, B, Saturn V, BRAINPOWER, Workzone
Tier 7: VeritY, Mechanical Showdown, omegasm, Tundra, DNA
Tier 8: Chaoz Airflow, Nitrogen, HeLL, Hellcat, Cyan
Tier 9: Lava Temple, Electric Landscape, InsanitY, acrylic canals, Epilogue
Tier 10: Fire Temple, Resurrection, Judgement, Left Behind, simulation swarm""",
"""Tier 11: Ditched Machine, Nine Circles, white women, Backasswards, METAL TEST
Tier 12: ThermoDynamix, Double Dash, Different Descent, 1326C, Solar Wind
Tier 13: Dance Massacre, Ultra Drivers, Future Funk, Eternelle Vehemence, Fnafbass
Tier 14: Fairydust, Forest Temple, Spectrum Switch, theyaremanycolors, mem
Tier 15: Psychosis, HASH, Mastermind, BLRPL LGHTS, Cubic Force """,
"""Tier 16: Windy Landscape, Magma Bound, CHROMA, FFFFFF, Wake Up Call
Tier 17: Colorful OverNight, 8o, Firewall, Night Terrors, Ultrachromatic
Tier 18: UltraSonic, The Secret Box, Leyak, arcane ascent, Gumshot
Tier 19: Crimson Clutter, Cyber Chaos, Ulon, CraZy III, Dustmuncher
Tier 20: ICE Carbon Diablo X, Gunslinga Corridor, Thanatophobia, Mint Candy, in canon""",
"""Tier 21: Cataclysm, HyperSonic, Incipient, STRATUS, Maths
Tier 22: Retention, HURRICANE, Crowd Control, sunburn, Cupid
Tier 23: Niflheim, Glide, Concaved Memories, Maybe Possibly Thing, U235
Tier 24: Bloodbath, Conical Depression, Triple Six, Prismatic Haze, burn to dust
Tier 25: Phobos, Sakupen Hell, Blade of Justice, Artifice, hot rod""",
"""Tier 26: Athanatos, SubSonic, Carnage Mode, Anoxysm, SPEEDRUN
Tier 27: Black Blizzard, Ziroikabi, Void Wave, Surge of the Shield, DMG CTRL
Tier 28: Artificial Ascent, Bausha Vortex, Killbot, Crystal, Edelweiss
Tier 29: Erebus, Chromatic Haze, Timor, Kuzureta, Nightshade
Tier 30: Sonic Wave, Gamma, Cybernetic Crescent, Sink, Shmarley Ville""",
"""Tier 31: Plasma Pulse Finale, Arctic Lights, Spectrum Cyclone, Nhelv, AKIRA
Tier 32: Bloodlust, Sigma, Congregation, Sazerix, Fog
Tier 33: Ragnarok, Cognition, The Rupture, RUST, Coral Cave
Tier 34: Renevant, Hard Machine, Sky Shredder, NEUTRA, ConClusion
Tier 35: Tartarus, The Golden, Oblivion, Verdant Landscape, Critical Heat""",
"""Tier 36: walter white, Edge of Destiny, Midnight, The Yangire, Collapse
Tier 37: poocubed, MINUSdry, Solar Flare, Saul Goodman, COMBUSTION
Tier 38: Slaughterhouse , Abyss of Darkness, Kyouki, Deimos, Menace
Tier 39: Acheron, Tidal Wave, Nullscapes, Anathema, andromeda"""
]

REF_NLW = [
"""Fuck: Eon, Game Over, Place, and most 2-players
Beginner: Acu, Cataclysm, HyperSonic, troll level
Easy: Crowd Control, Napalm, Retention, reverence
Medium: Maybe Possibly Thing, Niflheim,
Hard: aftermath, Prismatic Haze
Very Hard: Blade of Justice, Bloodbath""",
"""Insane: Athanatos, Worse Trip,
Extreme: Bausha Vortex, Black Blizzard
Remorseless: Artificial Ascent, Digital Descent
Relentless: Erebus, Sonic Wave, Yatagarasu
Terrifying: Sink, Wasureta""",
"""Catastrophic: kowareta, Plasma Pulse Finale
Inexorable: Bloodlust
Excruciating: Cognition, Crimson Planet
Merciless: Will be created when Zodiac falls off because the game is impossible""",
"""Low End: Beginner - Medium
Low-Mid Range: Medium - Hard
Mid Range: Hard - Insane
Mid-High Range: Insane - Relentless
High End: Relentless - Excruciating
Unknown: either below 3 options or all over the place
New Rates: Levels that were rated (or re-rated) recently
Potential Extremes: May or may not actually be extreme demons."""
]

REF_LW = [
"""Unfathomable (TSII) > Nightmare (Tidal Wave) > Unreal (Acheron)
    > Menacing (Slaughterhouse) > Demonic (Firework)
    > Apocalyptic (Edge of Destiny) > Monstrous (Tartarus)
    > Merciless (Zodiac)"""
]

REF_IDS = [
"""Fuck: Buff This, Denouement, Invisible Deadlocked
Beginner: Stalemate, Windy Landscape
Easy: Lit Fuse, Supersonic
Medium: Acropolis, Hyperio Technia, Night Terrors
Hard: The Secret Box, Sonic Wave Unlimited
Very Hard: Leyak, Spectral Tentation
Insane: ICE Carbon Diablo X, Quest for Perfection
Extreme: Edens Blessing, The End, Thanatophobia"""
]

REF_HDS = [
"""WARNING: not all picked by HDS team
Fuck: Larga Espera, Tidal Line, GD10
Demote: Spherio, Emerald Realm, PoisonGate
Easy: Nine Circles, pg clubstep, white women
Medium: Forsaken Neon, TOE III, ThermoDynamix
Hard: CraZy, Dance Massacre, Fairydust
Very Hard: Forest Temple, Breakthrough, Kitty
Insane: Mastermind, Psychosis, Spectrum Switch
Extreme: Diffuse, I Cant Fix You, Anya II"""
]

REF_PDIFF = [
"""All below are BASELINEs for the represented tier, meaning the levels in that tier should be HARDER the baseline
1 - BEGINNER: Moongrinder
2 - EASY: Jet Lag
3 - MODERATE: Aethos
4 - INTERMEDIATE: Switchscapes
5 - TOUGH: I wanna be the guy
6 - CHALLENGING: Tower of Infinity
7 - DIFFICULT: radio tower""",
"""All below are BASELINEs for the represented tier, meaning the levels in that tier should be HARDER the baseline
8 - FORMIDABLE: The Abyss
9 - CRUEL: Free Solo
10 - INSANE: Kill The Panas 2
11 - DEADLY: Null
12 - EXTREME: CONVOLUTION
13 - TERRIFYING: Diamonds For Dashers
14 - BRUTAL: Nothing yet..."""
]

def pagehint(page: int, pages: int) -> str:
    return f"\n当前处于第{page}页，共{pages}页"

@references.handle()
async def handle_references(arg: Message = CommandArg()) -> None:  # noqa: C901
    args = arg.extract_plain_text().strip().split()
    if len(args) == 0:
        await references.finish("use *references nlw/plat/gddl/hds/ids <page>")
        return
    name = args[0].lower().strip()
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    if name not in ["nlw", "gddl", "nlw", "ids", "hds", "lw", "aredl", "plat"]:
        await references.finish("use *references nlw/plat/gddl/hds/ids <page>")
        return
    if name == "aredl":
        await references.finish("AREDL是实时变化的，我不可能给你提供一个不会移动的参照线，建议手动使用*gdsearch搜索知名关的排名")
    elif name == "gddl":
        if page > len(REF_GDDL):
            await references.finish(f"你输入的页码数超过了总页数（共{len(REF_GDDL)}页，5个Tier一页），请重试")
        else:
            await references.finish(REF_GDDL[page-1] + pagehint(page,len(REF_GDDL)))
    elif name == "nlw":
        if page > len(REF_NLW):
            await references.finish(f"你输入的页码数超过了总页数（共{len(REF_NLW)}页），请重试")
        else:
            await references.finish(REF_NLW[page-1] + pagehint(page,len(REF_NLW)))
    elif name == "lw":
        await references.finish(REF_LW[0])
    elif name == "ids":
        await references.finish(REF_IDS[0])
    elif name == "hds":
        await references.finish(REF_HDS[0])
    elif name == "plat":
        if page > len(REF_PDIFF):
            await references.finish(f"你输入的页码数超过了总页数（共{len(REF_PDIFF)}页），请重试")
        else:
            await references.finish(REF_PDIFF[page-1] + pagehint(page,len(REF_PDIFF)))

