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

    usage: str  # 一行用法，比如 "*jwz 内容 [时长]"
    summary: str  # 命令列表里那一行的短说明
    detail: str  # *help 命令名 展开的正文
    category: str  # 归到哪个分类
    examples: tuple[str, ...] = ()
    constraints: str = ""  # 白名单群 / 冷却 / 依赖外部服务
    aliases: tuple[str, ...] = ()
    prefix: str = "*"  # 大部分是 *，戳一戳没有前缀


CATEGORIES: dict[str, str] = {
    "gd": "GD 关卡查询",
    "guess": "猜图",
    "fun": "娱乐杂项",
    "ai": "AI 聊天",
}


COMMANDS: dict[str, Cmd] = {
    # ---------------------------------------------------------------- gd
    "gdsearch": Cmd(
        usage="*gdsearch 关卡名或id",
        summary="基于GDDL的demon搜索",
        category="gd",
        detail=(
            "基于 GDDL 进行仅限demon的关卡查询，结合AREDL/NLW等其他来源的信息；\n"
            "结果以图片化形式输出，包含所有能查询到且方便排版的内容\n"
            "如果想要查询nondemon关卡，可以使用*gdfullsearch\n"
            "5 位以上纯数字会自动识别为关卡 id 并进行直接id搜索"
        ),
        examples=("*gdsearch Acu", "*gdsearch 61079355"),
    ),
    "gdfullsearch": Cmd(
        usage="*gdfullsearch 关键词 [-a] [-d [难度]] [-u 难度]",
        summary="基于GD服务器的关卡搜索",
        category="gd",
        detail=(
            "从gd服务器直接获取数据进行关卡搜索\n"
            "默认只搜索 rated 关卡\n"
            "-a　　　搜索全部关卡，包括unrate\n"
            "-d [难度]　只搜 demon。难度可省略，也可写 1-5 或\n"
            "　　　　　easy / medium / hard / insane / extreme\n"
            "-u 难度　只搜非 demon。写 0-5 或\n"
            "　　　　　auto / easy / normal / hard / harder / insane\n"
            "-d 和 -u 不能同时使用（废话）。\n"
            "一页列出 10 条内容：回复序号选中、回复下一页/上一页翻页，回复结束取消。"
        ),
        examples=("*gdfullsearch bloodbath", "*gdfullsearch stereo -a -u 0"),
        constraints="2 分钟不操作自动结束搜索",
    ),
    "gdratings": Cmd(
        usage="*gdratings 关卡名或id [-s 排序] [-asc] [-v]",
        summary="查看 GDDL 上的评分详情",
        category="gd",
        detail=(
            "列出这关在 GDDL 上每个人提交的 tier 和 enjoyment，\n"
            "完全的vibe coding产物！\n"
            "-s 排序　tier / enj / date / progress / attempts\n"
            "-asc　　正序（默认倒序）\n"
            "-v　　　只看通关的人\n"
            "一页列出 10 条内容：回复序号选中、回复下一页/上一页翻页，回复结束取消。\n"
            "如果有多个关卡重名，会列出所有相关关卡的id，请使用id重新搜索"
        ),
        examples=("*gdratings Sky Mirage", "*gdratings 137369971 -s enj -asc"),
        constraints="2 分钟不操作自动结束搜索",
    ),
    "gduser": Cmd(
        usage="*gduser 用户名",
        summary="查 GD 玩家资料",
        category="gd",
        detail=(
            "查一个 GD 玩家的star、moon、demon 数、creator point，\n"
            "以及 classic / platformer 各难度的通关数拆分。\n"
            "请输入游戏内用户名。"
        ),
        examples=("*gduser yeshengxiaozu",),
    ),
    "gdicon": Cmd(
        usage="*gdicon 用户名 [gamemode] [-a]",
        summary="看玩家的图标",
        category="gd",
        detail=(
            "把玩家某个 gamemode 的图标画出来，用的是他自己的图标 id 和配色。\n"
            "gamemode 可选：cube / ship / ball / ufo / wave / robot /\n"
            "spider / swing / jetpack，不写默认 cube。\n"
            "ufo 可以写 bird，wave 可以写 dart，中文也认（飞碟 / 秋千 之类）。\n"
            "加 -a 把九个 gamemode 拼成一张图发出来，不会刷屏。"
        ),
        examples=("*gdicon RobTop", "*gdicon RobTop ship", "*gdicon RobTop -a"),
    ),
    "gd随机推关": Cmd(
        usage="*gd随机推关 (最低)tier [最高tier] [最低enj] [最高enj]",
        summary="按 tier / enjoyment 随机推一关",
        category="gd",
        detail=(
            "在指定条件里随机挑一关推给你。\n"
            "第 1 个是 tier 下限（1-39），必填；只给一个参数时视为上下界相同。\n"
            "第 2 个是 tier 上限\n"
            "第 3、4 个是 enjoyment 的下限和上限（0-10，可选）\n"
        ),
        examples=(
            "*gd随机推关 20",
            "*gd随机推关 15 20",
            "*gd随机推关 15 20 7",
            "*gd随机推关 0 15 0 6",
        ),
    ),
    "dailydemon": Cmd(
        usage="*dailydemon",
        summary="今日关卡，每天换一关",
        category="gd",
        detail=(
            "每天从 GDDL 挑一关推给大家，条件是\n"
            "tier 1-9、enjoyment 7 分以上、提交数 10 条以上，\n"
            "无人工干涉，每天更换，随到搞笑关别打我"
        ),
        examples=("*dailydemon",),
    ),
    "references": Cmd(
        usage="*references 表名 [页码]",
        summary="各难度表的参考线",
        category="gd",
        detail=(
            "看各个难度表每一档大概的参考线，使用官方提供或知名关卡。\n"
            "表名：gddl / nlw / lw / ids / hds / plat\n"
            "gddl 有 8 页（5 个 tier 一页），nlw 4 页，plat 2 页，其余 1 页。\n"
            "aredl 会实时变化无法提供固定参考线，建议直接 *gdsearch 查知名关的排名。"
        ),
        examples=("*references gddl 5", "*references nlw"),
    ),
    "gdsearchhelp": Cmd(
        usage="*gdsearchhelp",
        summary="gd 命令的简要说明",
        category="gd",
        detail="gdsearch 相关命令的简短说明，内容和 *help gdsearch 差不多。",
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
            "「你说的对，但是……」那套文学，在几段写死的文案里随机发一段。\n不吃参数。"
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
}
