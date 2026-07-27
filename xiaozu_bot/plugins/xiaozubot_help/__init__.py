import unicodedata

from nonebot import get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import Message
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .commands import CATEGORIES, COMMANDS, Cmd
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


def _alias_map() -> dict[str, str]:
    """别名 -> 正式命令名"""
    out: dict[str, str] = {}
    for name, cmd in COMMANDS.items():
        for alias in cmd.aliases:
            out[alias.lower()] = name
    return out


ALIASES = _alias_map()

# 分类也能直接查，比如 *help gd，中文写法一并认
CATEGORY_ALIASES: dict[str, str] = {
    "gd": "gd", "关卡": "gd", "搜索": "gd", "search": "gd",
    "guess": "guess", "猜图": "guess", "猜": "guess",
    "fun": "fun", "娱乐": "fun", "杂项": "fun",
    "ai": "ai", "语音": "ai",
    "demon": "demon", "恶魔": "demon",
    "admin": "admin", "管理": "admin",
}


def _by_category(cat: str) -> list[tuple[str, Cmd]]:
    return [(n, c) for n, c in COMMANDS.items() if c.category == cat]


def _width(text: str) -> int:
    """按显示宽度算，中日韩字符占两格"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """按显示宽度右侧补空格，不能用 ljust —— 它是按字符数算的，中文会歪"""
    return text + " " * max(0, width - _width(text))


def _command_lines(cmds: list[tuple[str, Cmd]], indent: str = "") -> list[str]:
    labels = [(c.prefix + n, c) for n, c in cmds]
    width = max(_width(label) for label, _ in labels)
    return [f"{indent}{_pad(label, width)}  {c.summary}" for label, c in labels]


def _overview() -> str:
    lines = [
        "小小卒的命令基本都以 * 开头",
        "看某条命令具体怎么用：*help 命令名，比如 *help gdfullsearch",
        "按分类看：*help " + " / ".join(CATEGORIES),
        "用户群 1035708051，功能建议可以在里面提",
    ]
    for cat, title in CATEGORIES.items():
        cmds = _by_category(cat)
        if not cmds:
            continue
        lines.append("")
        lines.append(f"【{title}】")
        lines += _command_lines(cmds, indent="  ")
    return "\n".join(lines)


def _render(name: str, cmd: Cmd) -> str:
    parts = [f"【{cmd.prefix}{name}】{cmd.summary}", "", cmd.usage, "", cmd.detail]
    if cmd.aliases:
        # 有的别名自带前缀（比如 。恶魔帮助），别再补一个
        parts += [
            "",
            "别名：" + "、".join(
                a if a[:1] in "*.。" else cmd.prefix + a for a in cmd.aliases
            ),
        ]
    if cmd.examples:
        parts += ["", "例子："] + [f"  {e}" for e in cmd.examples]
    if cmd.constraints:
        parts += ["", "限制：" + cmd.constraints]
    return "\n".join(parts)


def _render_category(cat: str) -> str:
    cmds = _by_category(cat)
    lines = [f"【{CATEGORIES[cat]}】", ""]
    lines += _command_lines(cmds)
    lines += ["", "看单条的详细用法：*help 命令名"]
    return "\n".join(lines)


@xiaozubothelp.handle()
async def handle_help(arg: Message = CommandArg()) -> None:
    query = arg.extract_plain_text().strip().lstrip("*.。").lower()

    if not query:
        await xiaozubothelp.finish(_overview())

    # 先当命令名查，再当别名，最后才当分类 ——
    # game 既是命令又像分类名，命令优先
    name = query if query in COMMANDS else ALIASES.get(query)
    if name:
        await xiaozubothelp.finish(_render(name, COMMANDS[name]))

    cat = CATEGORY_ALIASES.get(query)
    if cat and _by_category(cat):
        await xiaozubothelp.finish(_render_category(cat))

    guesses = [n for n in COMMANDS if query in n or n in query][:5]
    tip = ("\n你是不是想找：" + "、".join("*" + g for g in guesses)) if guesses else ""
    await xiaozubothelp.finish(
        f"没有「{query}」这个命令或分类，*help 看全部。{tip}"
    )


def pagehint(page: int, pages: int) -> str:
    return f"\n当前处于第{page}页，共{pages}页"


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

@references.handle()
async def handle_references(arg: Message = CommandArg()) -> None:
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
        # 页码是 1 开始的，下界必须挡掉：`"0".isdigit()` 是 True，
        # 不挡的话 REF_GDDL[0-1] 会翻出最后一页，提示语却写着「第0页」
        if page < 1 or page > len(REF_GDDL):
            await references.finish(f"你输入的页码数超出范围（共{len(REF_GDDL)}页，5个Tier一页），请重试")
        else:
            await references.finish(REF_GDDL[page-1] + pagehint(page,len(REF_GDDL)))
    elif name == "nlw":
        if page < 1 or page > len(REF_NLW):
            await references.finish(f"你输入的页码数超出范围（共{len(REF_NLW)}页），请重试")
        else:
            await references.finish(REF_NLW[page-1] + pagehint(page,len(REF_NLW)))
    elif name == "lw":
        await references.finish(REF_LW[0])
    elif name == "ids":
        await references.finish(REF_IDS[0])
    elif name == "hds":
        await references.finish(REF_HDS[0])
    elif name == "plat":
        if page < 1 or page > len(REF_PDIFF):
            await references.finish(f"你输入的页码数超出范围（共{len(REF_PDIFF)}页），请重试")
        else:
            await references.finish(REF_PDIFF[page-1] + pagehint(page,len(REF_PDIFF)))

