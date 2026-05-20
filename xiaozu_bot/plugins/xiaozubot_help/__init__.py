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

@xiaozubothelp.handle()
async def handle_help() -> None:
    await xiaozubothelp.finish(
        """欢迎使用小小卒！命令以*开头
用户群：1035708051可以在里面对bot功能提建议什么的
help：本命令！
小小卒对你回应按按钮：非法输入
gdsearch：搜一个gd图（因为技术原因限制，暂时只能搜demon）
random：在你输入的选项中随机抽取一个
say：让机器人开口说话？
ai：你见过的最弱智的ai聊天

ultra：不要再！
say：让机器人说一句话？（不定时开放）
jwz x：我能在患有健忘症的情况下患有健忘症吗？
guess(_start(_hard/_ultra)/giveup)：猜酱图 hard题目更小
按钮是答错了，问号是目前没有题（一般是题目被答过）
zhua：抓一个小卒？"""
    )

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
Very Hard: Blade of Justice, Bloodbath
Insane: Athanatos, Worse Trip""",
"""Extreme: Bausha Vortex, Black Blizzard
Remorseless: Artificial Ascent, Digital Descent
Relentless: Erebus, Sonic Wave, Yatagarasu
Terrifying: Sink, Wasureta
Catastrophic: kowareta, Plasma Pulse Finale
Inexorable: Bloodlust
Excruciating: Cognition, Crimson Planet"""
]

REF_LW = [
"""Nightmare (TSII) > Unreal (Acheron) > Menacing (Slaughterhouse)
    > Demonic (Firework) > Apocalyptic (Edge of Destiny)
    > Monstrous (Tartarus) > Merciless (Zodiac) > Excruciating (Cold Sweat)
PS: 截至Cold Sweat掉榜前，LW一共剩下6个Excruciating还没掉"""
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
    if name not in ["nlw", "gddl", "nlw", "ids", "hds", "lw", "aredl"]:
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

