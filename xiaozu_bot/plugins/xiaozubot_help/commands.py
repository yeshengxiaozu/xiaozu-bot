"""每条命令的说明。

加新命令的时候往 COMMANDS 里补一条就行，*help 会自动带上，
不用再去改 help 的正文。

detail 尽量写清楚每个参数收什么值、默认是什么、有什么限制，
因为 *help 命令名 出来的就是这段。

不往这里写的：
- 超级用户专用的（那些是给管理员自己用的）
- 只对个别人开放的
- 已经下线的（蓝莓、轮盘那套）
- 注册了但没有处理函数的
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Cmd:
    """一条命令的说明"""

    usage: str                              # 一行用法，比如 "*jwz 内容 [时长]"
    summary: str                            # 命令列表里那一行的短说明
    detail: str                             # *help 命令名 展开的正文
    category: str                           # 归到哪个分类
    examples: tuple[str, ...] = ()
    constraints: str = ""                   # 白名单群 / 冷却 / 依赖外部服务
    aliases: tuple[str, ...] = ()
    prefix: str = "*"                       # 大部分是 *，恶魔帮助是 .，戳一戳没有前缀


CATEGORIES: dict[str, str] = {
    "gd": "GD 关卡查询",
    "guess": "猜图",
    "fun": "娱乐杂项",
    "ai": "AI 聊天",
    "demon": "恶魔轮盘",
}


COMMANDS: dict[str, Cmd] = {
    # ---------------------------------------------------------------- gd
    "gdsearch": Cmd(
        usage="*gdsearch 关卡名或id",
        summary="查关卡（本地榜单）",
        category="gd",
        detail=(
            "查本地收录的难度表：GDDL、NLW、IDS、LW、HDS，还有 plat chart。\n"
            "结果渲染成一张图，上面有 tier、enjoyment、AREDL 排名、技能标签这些。\n"
            "收录的基本都是 demon，普通关卡多半查不到，那种用 *gdfullsearch。\n"
            "5 位以上纯数字当关卡 id 直接查；同名搜到多个会列出来，回复序号选，"
            "30 秒不选自动取消。"
        ),
        examples=("*gdsearch Tartarus", "*gdsearch 59075347"),
    ),
    "gdfullsearch": Cmd(
        usage="*gdfullsearch 关键词 [-a] [-d [难度]] [-u 难度]",
        summary="查关卡（直连 GD 服务器）",
        category="gd",
        detail=(
            "直接问 GD 官方服务器要数据，服务器上有的关卡都能搜到，不限 demon。\n"
            "默认只搜 rated（评过级的）。\n"
            "-a　　　搜全部关卡，包括没评级的\n"
            "-d [难度]　只搜 demon。难度可省略，也可写 1-5 或\n"
            "　　　　　easy / medium / hard / insane / extreme\n"
            "-u 难度　只搜非 demon。写 0-5 或\n"
            "　　　　　auto / easy / normal / hard / harder / insane（0 就是 auto）\n"
            "-d 和 -u 不能一起用。\n"
            "一页 10 条：回复序号选中、n 下一页、p 上一页、结束 取消。"
        ),
        examples=("*gdfullsearch bloodbath", "*gdfullsearch stereo -a -u 0"),
        constraints="2 分钟不操作自动结束翻页",
    ),
    "gdratings": Cmd(
        usage="*gdratings 关卡名或id [-s 排序] [-asc] [-v]",
        summary="看 GDDL 上大家给的评分",
        category="gd",
        detail=(
            "列出这关在 GDDL 上每个人提交的 tier 和 enjoyment，\n"
            "就是网页上「Submitted ratings」那块。\n"
            "-s 排序　tier / enj / date / progress / attempts / rr\n"
            "-asc　　正序（默认倒序）\n"
            "-v　　　只看通关了的人\n"
            "一页 10 条，n 下一页 / p 上一页 / 结束 取消。\n"
            "给名字会去 GDDL 上找对应关卡，撞名了会把候选连 id 列出来让你重查。"
        ),
        examples=("*gdratings Tartarus", "*gdratings 10565740 -s enj -asc"),
        constraints="2 分钟不操作自动结束翻页",
    ),
    "gduser": Cmd(
        usage="*gduser 用户名",
        summary="查 GD 玩家资料",
        category="gd",
        detail=(
            "查一个 GD 玩家的星星、月亮、demon 数、creator point，\n"
            "以及 classic / platformer 各难度的通关数拆分。\n"
            "按游戏内用户名查。"
        ),
        examples=("*gduser Riot",),
    ),
    "gd随机推关": Cmd(
        usage="*gd随机推关 低 [高]",
        summary="按 tier 区间随机推一关",
        category="gd",
        detail=(
            "在指定的 GDDL tier 区间里随机挑一关推给你。\n"
            "只给一个数字就是只推那个 tier，给两个就是区间。\n"
            "（enjoyment 筛选没做）"
        ),
        examples=("*gd随机推关 20", "*gd随机推关 15 20"),
    ),
    "references": Cmd(
        usage="*references 表名 [页码]",
        summary="各难度表的参考线",
        category="gd",
        detail=(
            "看各个难度表每一档大概什么水平，用知名关卡当标尺。\n"
            "表名：gddl / nlw / lw / ids / hds / plat\n"
            "gddl 有 8 页（5 个 tier 一页），nlw 4 页，plat 2 页，其余 1 页。\n"
            "aredl 是实时变的给不了固定参考线，直接 *gdsearch 查知名关的排名。"
        ),
        examples=("*references gddl 5", "*references nlw"),
    ),
    "gdsearchhelp": Cmd(
        usage="*gdsearchhelp",
        summary="gd 命令的简要说明",
        category="gd",
        detail="gd 相关命令的简短说明，内容和 *help gd 差不多。",
    ),
    # ---------------------------------------------------------------- guess
    "guess_start": Cmd(
        usage="*guess_start",
        summary="出题，截图 256×256",
        category="guess",
        detail=(
            "随机挑一张地图，随机裁一块 256×256 的截图让你猜。\n"
            "会自动跳过纯色/没信息的区域，所以不会给你一块纯黑。\n"
            "群里是整个群共享一道题：谁都能答、谁都能放弃，\n"
            "上一题没结束要先 *guess_giveup 才能出新题。"
        ),
        examples=("*guess_start",),
        constraints="同一个群（或私聊）45 秒出题冷却，冷却中只会给你的消息回一个叉",
    ),
    "guess_start_hard": Cmd(
        usage="*guess_start_hard",
        summary="出题，截图 128×128",
        category="guess",
        detail="和 *guess_start 一样，只是截图变成 128×128，面积只有四分之一。",
        examples=("*guess_start_hard",),
        constraints="和 *guess_start 共用同一个 45 秒冷却",
    ),
    "guess_start_ultra": Cmd(
        usage="*guess_start_ultra",
        summary="出题，截图 64×64",
        category="guess",
        detail="和 *guess_start 一样，截图 64×64，三档里最小的。",
        examples=("*guess_start_ultra",),
        constraints="和 *guess_start 共用同一个 45 秒冷却",
    ),
    "guess": Cmd(
        usage="*guess 答案",
        summary="回答当前猜图题",
        category="guess",
        detail=(
            "回答当前这道题。必须用这个命令，直接在群里发地图名是不算的。\n"
            "比对时会忽略大小写、空格和常见标点。\n"
            "答对：把原图发出来并用红框标出截图是从哪截的。\n"
            "答错：给你的消息回一个叉，偶尔会把题图再发一遍。\n"
            "注意：大部分地图要用简称/别名才判对，写完整的官方全名多半不认。"
        ),
        examples=("*guess 序章", "*guess prologue"),
        constraints="答题本身没有冷却，出题冷却期内也能答",
    ),
    "guess_giveup": Cmd(
        usage="*guess_giveup",
        summary="放弃并公布答案",
        category="guess",
        detail=(
            "放弃当前这道题，公布答案并用红框标出截图位置。\n"
            "题目是全群共享的，所以不是出题的人也能放弃。\n"
            "放弃不计入统计，但放完才能出下一题。"
        ),
        examples=("*guess_giveup",),
        constraints="出题后 45 秒内不能放弃",
    ),
    "guess_count": Cmd(
        usage="*guess_count",
        summary="看全服猜测统计",
        category="guess",
        detail=(
            "看全服累计猜了多少次、猜对多少道。\n"
            "是所有群加起来的总数，不分群也不分人，看不到个人战绩。\n"
            "三档难度记在同一组计数里，出题和放弃不计入。"
        ),
        examples=("*guess_count",),
    ),
    # ---------------------------------------------------------------- fun
    "jrrp": Cmd(
        usage="*jrrp",
        summary="今日人品，每天一次",
        category="fun",
        detail=(
            "抽一个 1-100 的今日人品，按分数给一句点评。\n"
            "每人每天一次，当天再发只会把之前的结果再念一遍。\n"
            "每天零点重置。"
        ),
        examples=("*jrrp",),
        constraints="只能在群里用",
    ),
    "zhua": Cmd(
        usage="*zhua",
        summary="随机抓一只小卒",
        category="fun",
        detail=(
            "从图库里随机抓一只小卒，带上名字和描述发给你。\n"
            "想看指定的那只用 *show 名字。"
        ),
        examples=("*zhua",),
        constraints="每人 10 分钟冷却，冷却中会告诉你还剩几秒",
    ),
    "show": Cmd(
        usage="*show 名称",
        summary="按名字看指定的小卒",
        category="fun",
        detail=(
            "按名字看某一只小卒，名字就是 *zhua 抓到时显示的那个。\n"
            "名字不区分大小写；写错了会提示你重输。"
        ),
        examples=("*show mc卒",),
    ),
    "random": Cmd(
        usage="*random 选项1 选项2 ...",
        summary="在选项里随机挑一个",
        category="fun",
        detail=(
            "在你给的几个选项里随机挑一个。选项用空格分开，至少给一个。\n"
            "纠结吃什么的时候用。"
        ),
        examples=("*random 吃饭 睡觉 打游戏",),
    ),
    "map": Cmd(
        usage="*map",
        summary="随机来一张地图",
        category="fun",
        detail="随机给一张地图名，不吃参数。",
        examples=("*map",),
    ),
    "jwz": Cmd(
        usage="*jwz 内容 [时长]",
        summary="健忘症通关体长文",
        category="fun",
        detail=(
            "发一段「我能在患有健忘症的情况下通关X吗？」的模板长文。\n"
            "第一个参数是游戏/作品名，必填。\n"
            "第二个参数是文中「推出已经有…了」的时长，不给就随机一个。"
        ),
        examples=("*jwz 蔚蓝", "*jwz 几何冲刺 8年"),
        constraints="只能在群里用；参数太长（超过 100 字）会被忽略",
    ),
    "today": Cmd(
        usage="*today 词1 词2 词3",
        summary="今天是著名大神小作文",
        category="fun",
        detail=(
            "发一段「今天是著名(词1)大神(词2)(词3)的日子……」的祝福小作文。\n"
            "必须正好三个参数：词1 是领域或头衔，词2 是人名，词3 是事件或节日。\n"
            "多一个少一个都不行。"
        ),
        examples=("*today 蔚蓝 小卒 生日",),
        constraints="只能在群里用；参数太长（超过 100 字）会被忽略",
    ),
    "game": Cmd(
        usage="*game 编号(1~4)",
        summary="小游戏随机建议",
        category="fun",
        detail=(
            "给几类小游戏出「建议」，全是随机的，仅供参考。\n"
            "1 = 扑克：大于7/小于7、花色、或者两张牌\n"
            "2 = 恶魔轮盘：猜下一发是实弹还是虚弹\n"
            "3 = 最有潜力的擂台编号（1~10）\n"
            "4 = 今天宝藏埋在哪（三个 1~10 的数字）\n"
            "编号一定要带，不带会出错。"
        ),
        examples=("*game 2",),
        constraints="只能在群里用",
    ),
    "ultra": Cmd(
        usage="*ultra [事物 幕后黑手]",
        summary="不要再X了抵制体",
        category="fun",
        detail=(
            "发「不要再X了！X是Y研发的新型压片……」那段抵制体长文。\n"
            "不带参数发写死的原版。\n"
            "要套自己的就给两个参数：第一个是被抵制的东西，第二个是幕后黑手。\n"
            "只给一个参数不会有内容，等于提示你参数不够。"
        ),
        examples=("*ultra", "*ultra 原神 米哈游"),
        constraints="只能在群里用；参数太长（超过 100 字）会被忽略",
    ),
    "nsdd": Cmd(
        usage="*nsdd",
        summary="你说的对，但是……",
        category="fun",
        detail=(
            "「你说的对，但是……」那套文学，在几段写死的文案里随机发一段。\n"
            "不吃参数。"
        ),
        examples=("*nsdd",),
        constraints="只能在群里用",
    ),
    "news": Cmd(
        usage="*news",
        summary="更新公告",
        category="fun",
        aliases=("公告", "新闻"),
        detail="看最近的更新公告。不吃参数。",
        examples=("*news", "*公告"),
        constraints="只能在群里用",
    ),
    "戳一戳": Cmd(
        usage="戳一戳小小卒（不是命令）",
        summary="戳它它会戳回来",
        category="fun",
        prefix="",
        detail=(
            "群里戳一戳小小卒，它会戳回来，不发文字。\n"
            "这个不是命令，不用打字，用 QQ 的戳一戳功能就行。"
        ),
        constraints="只在群里有效；需要 QQ 客户端支持戳一戳",
    ),
    # ---------------------------------------------------------------- ai
    "ai": Cmd(
        usage="*ai 内容",
        summary="和小小卒聊天",
        category="ai",
        detail=(
            "和小小卒聊天，跑的是本地模型，别期待太高。\n"
            "会记住最近 5 轮对话当上下文，按群/按私聊分开记。"
        ),
        examples=("*ai 你好", "*ai 帮我想个网名"),
        constraints="个别群里禁用",
    ),
    # say / say_i 不往这里写：mlx_audio 在现在这台机器上跑不了，
    # 老的那套实现也已经废掉了，写进 help 只会让人白试。
    # ---------------------------------------------------------------- demon
    "betgame": Cmd(
        usage="*betgame",
        summary="加入 / 开始恶魔轮盘",
        category="demon",
        detail=(
            "加入本群的恶魔轮盘对局，一局两个人。\n"
            "第一个人进来是等待状态，第二个人进来就直接开局，\n"
            "随机决定先手、随机上弹、给双方发初始道具。\n"
            "开局用哪个模式看两人各自 *setmode 的设置：\n"
            "两人一样就用那个，不一样就在两者里随机抽一个。\n"
            "一个人等太久（10 分钟）会自动重置。"
        ),
        examples=("*betgame",),
        constraints="只在指定的几个群里能用",
    ),
    "setmode": Cmd(
        usage="*setmode 模式编号",
        summary="设置轮盘模式",
        category="demon",
        detail=(
            "设置你自己开局时用的模式，只收 0 / 1 / 2 三个值。\n"
            "0 = 普通：基础 15 个道具，血量上限 6，道具上限 6\n"
            "1 = 身份：全部 26 个道具，血量上限 10，道具上限 8，超过 12 轮进死斗\n"
            "2 = 膀胱：血量上限 16，道具上限 10，每轮多发道具，超过 5 轮就进死斗\n"
            "这个设置是跟人走的（不分群），下一局才生效，不影响正在进行的对局。\n"
            "注意两人设置不同的话实际模式是随机二选一，所以不保证用你设的那个。"
        ),
        examples=("*setmode 1",),
        constraints="只在指定的几个群里能用",
    ),
    "开枪": Cmd(
        usage="*开枪 自己|对方",
        summary="向自己或对方开枪",
        category="demon",
        aliases=("射击",),
        detail=(
            "参数只认「自己」和「对方」两个词。\n"
            "打自己：不管有没有实弹，回合都留在自己手里，可以接着行动。\n"
            "打对方：不管中不中都交出回合（对方被手铐/禁止卡拷住时除外）。\n"
            "命中伤害是 1 加上当前的加伤，开完枪加伤清零。\n"
            "子弹打光会自动换弹、轮数 +1、双方补道具。\n"
            "一方血量归零就结算。"
        ),
        examples=("*开枪 对方",),
        constraints="只在指定的几个群里能用；要在局内且轮到你；每步限时 10 分钟，超时判负",
    ),
    "使用": Cmd(
        usage="*使用 道具名",
        summary="使用一个道具",
        category="demon",
        aliases=("使用道具",),
        detail=(
            "用一个自己道具栏里的道具，参数是道具的中文名。\n"
            "通用道具 15 个：桃、医疗箱、放大镜、眼镜、手铐、禁止卡、欲望之盒、\n"
            "无中生有、小刀、酒、啤酒、刷新票、手套、骰子、墨镜。\n"
            "身份/膀胱模式还会多出 11 个：双转团、天秤、休养生息、玩具枪、烈弓、\n"
            "血刃、黑洞、金苹果、铂金草莓、肾上腺素、烈性TNT。\n"
            "医疗箱、无中生有、金苹果会让出回合，其余多数可以一回合连着用几个。\n"
            "看道具效果用 *恶魔道具。"
        ),
        examples=("*使用 放大镜",),
        constraints="只在指定的几个群里能用；要在局内且轮到你",
    ),
    "恶魔道具": Cmd(
        usage="*恶魔道具 [道具名|all]",
        summary="查道具效果",
        category="demon",
        detail=(
            "查道具是干什么用的，纯查询，不用在局内也能用。\n"
            "带道具名就回那一个的说明；\n"
            "写 all 或者什么都不写，就把全部 26 个道具的说明合并转发一份。"
        ),
        examples=("*恶魔道具 烈弓", "*恶魔道具 all"),
        constraints="只在指定的几个群里能用",
    ),
    "查看局势": Cmd(
        usage="*查看局势",
        summary="看当前对局状态",
        category="demon",
        detail=(
            "看当前对局：模式（进死斗会标出来）、本步剩余时间、轮数、\n"
            "双方的血量和道具、弹夹里还有几发几实弹、现在该谁动。\n"
            "对方被拷住、或者这颗子弹有加伤的时候会多显示一行。"
        ),
        examples=("*查看局势",),
        constraints="只在指定的几个群里能用；要在局内",
    ),
    "恶魔投降": Cmd(
        usage="*恶魔投降",
        summary="投降结束本局",
        category="demon",
        detail="认输，直接判自己负、对方胜，然后重置本群对局。不限回合，随时能投。",
        examples=("*恶魔投降",),
        constraints="要在局内",
    ),
    "恶魔帮助": Cmd(
        usage=".恶魔帮助",
        summary="恶魔轮盘指令速查",
        category="demon",
        prefix=".",
        aliases=("。恶魔帮助",),
        detail=(
            "恶魔轮盘的指令速查表。\n"
            "注意这条是点号开头（.恶魔帮助 或 。恶魔帮助），不是星号，\n"
            "而且要完整输入，后面不能带别的字。"
        ),
        examples=(".恶魔帮助",),
        constraints="只在指定的几个群里能用",
    ),
}
