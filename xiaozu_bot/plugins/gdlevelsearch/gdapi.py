import base64
from dataclasses import dataclass, field
from typing import Any, Final, Optional
from urllib.parse import unquote

import requests
from nonebot import logger

DEMON_STARS = 10
LENGTH_PLAT = 5

# boomlings 是 RobTop 自己的服务器，经常半死不活。
# requests 默认是不超时的，一个卡住的连接能让调用方等到天荒地老。
GD_TIMEOUT = 15

# GD 服务器一页固定给 10 条
GD_PAGE_SIZE = 10
# 响应里的 total 到这个数就是封顶了，不是真实条数。
# 实测：搜 bloodbath 不加筛选 total=9999，加 star=1 之后 total=5。
GD_TOTAL_CAP = 9999

OFFICIAL_SONG_MAP = {
    -1: ("Practice: Stay Inside Me", "OcularNebula"),
    0: ("Stereo Madness", "Foreverbound"),
    1: ("Back on Track", "DJVI"),
    2: ("Polargeist", "Step"),
    3: ("Dry Out", "DJVI"),
    4: ("Base after Base", "DJVI"),
    5: ("Cant Let Go", "DJVI"),
    6: ("Jumper", "Waterflame"),
    7: ("Time Machine", "Waterflame"),
    8: ("Cycles", "DJVI"),
    9: ("xStep", "DJVI"),
    10: ("Clutterfunk", "Waterflame"),
    11: ("Theory of Everything", "DJ-Nate"),
    12: ("Electroman Adventures", "Waterflame"),
    13: ("Clubstep", "DJ-Nate"),
    14: ("Electrodynamix", "DJ-Nate"),
    15: ("Hexagon Force", "Waterflame"),
    16: ("Blast Processing", "Waterflame"),
    17: ("Theory of Everything 2", "DJ-Nate"),
    18: ("Geometrical Dominator", "Waterflame"),
    19: ("Deadlocked", "F-777"),
    20: ("Fingerdash", "MDK"),
    21: ("Dash", "MDK"),
    22: ("Explorers", "Hinkik"),
    23: ("The Seven Seas", "F-777"),
    24: ("Viking Arena", "F-777"),
    25: ("Airborne Robots", "F-777"),
    26: ("Secret", "RobTop"),
    27: ("Payload", "Dex Arson"),
    28: ("Beast Mode", "Dex Arson"),
    29: ("Machina", "Dex Arson"),
    30: ("Years", "Dex Arson"),
    31: ("Frontlines", "Dex Arson"),
    32: ("Space Pirates", "Waterflame"),
    33: ("Striker", "Waterflame"),
    34: ("Embers", "Dex Arson"),
    35: ("Round 1", "Dex Arson"),
    36: ("Monster Dance Off", "F-777"),
    37: ("Press Start", "MDK"),
    38: ("Nock Em", "Bossfight"),
    39: ("Power Trip", "Boom Kitty"),
}


class GDLevel:
    """Geometry Dash 关卡数据类"""

    FIELD_MAP: Final = {
        1: ("level_id", int),
        2: ("level_name", str),
        3: ("description", "base64"),
        4: ("level_string", str),
        5: ("version", int),
        6: ("player_id", int),
        8: ("difficulty_denominator", int),
        9: ("difficulty_numerator", int),
        10: ("downloads", int),
        12: ("official_song", int),
        13: ("game_version", int),
        14: ("likes", int),
        15: ("length", int),
        17: ("is_demon", bool),
        18: ("stars", int),
        19: ("feature_score", int),
        25: ("is_auto", bool),
        27: ("password", str),
        28: ("upload_date", str),
        29: ("update_date", str),
        30: ("copied_id", int),
        31: ("is_two_player", bool),
        35: ("custom_song_id", int),
        36: ("extra_string", str),
        37: ("coins", int),
        38: ("verified_coins", bool),
        39: ("stars_requested", int),
        40: ("low_detail_mode", bool),
        41: ("daily_number", int),
        42: ("epic", int),
        43: ("demon_difficulty", int),
        44: ("is_gauntlet", bool),
        45: ("objects", int),
        46: ("editor_time", int),
        47: ("editor_time_copies", int),
        48: ("settings_string", str),
        52: ("song_ids", str),
        53: ("sfx_ids", str),
        54: ("unknown54", int),
        57: ("verification_time", int),
    }

    level_id: int
    level_name: str
    description: str
    level_string: Optional[str] = None
    version: int
    player_id: int
    difficulty_denominator: int
    difficulty_numerator: int
    downloads: int
    official_song: int
    game_version: int
    likes: int
    length: int
    is_demon: bool
    stars: int = 0
    feature_score: int
    is_auto: bool
    password: Optional[str] = None
    upload_date: Optional[str] = None
    update_date: Optional[str] = None
    copied_id: int
    is_two_player: bool
    custom_song_id: int
    extra_string: str
    coins: int
    verified_coins: bool
    stars_requested: int
    low_detail_mode: Optional[bool] = None
    daily_number: Optional[int] = None
    epic: Optional[int] = None # The epic rating for the level. 0 = none, 1 = epic, 2 = legendary, 3 = mythic.
    demon_difficulty: int
    is_gauntlet: bool
    objects: int
    editor_time: int
    editor_time_copies: int
    song_ids: Optional[str] = None # Comma-Separated List
    sfx_ids: Optional[str] = None # Comma-Separated List
    verification_time: Optional[int] = None

    creator_name: Optional[str] = None
    song_info: Optional[dict[str, Any]] = None

    XOR_KEY = "26364"

    def __init__(self) -> None:
        for attr, _ in self.FIELD_MAP.values():
            setattr(self, attr, None)
        self.creator_name = None
        self.song_info = None

    @classmethod
    def from_server_response(cls, response: str) -> "GDLevel":
        """解析 key:value:key:value 字符串"""
        instance = cls()
        pairs = parse_server_key_value_pairs(response)
        for key, (attr, typ) in cls.FIELD_MAP.items():
            if key not in pairs:
                continue
            setattr(instance, attr, _parse_server_value(pairs[key], typ))
        return instance

    @classmethod
    def from_string(cls, response: str) -> "GDLevel":
        """从原始字符串直接构建 GDLevel 对象。"""
        return cls.from_server_response(response)

    @property
    def song_id(self) -> Optional[int]:
        """返回当前歌曲 ID：custom song 直接返回 custom_song_id，official song 返回负值。"""
        if self.custom_song_id and self.custom_song_id != 0:
            return self.custom_song_id
        if self.official_song is not None:
            return -self.official_song
        return None

    @property
    def song_name(self) -> Any:
        """返回歌曲名称的可读字符串，若无信息则返回 Unknown"""
        # 优先自定义歌曲
        if self.song_info:
            return self.song_info.get("name")
        # 官方歌曲
        off_id = self.official_song
        # 部分关卡可能 custom_song_id 为 0 但官方歌曲空，需要检查
        if off_id is not None and off_id in OFFICIAL_SONG_MAP:
            name, _author = OFFICIAL_SONG_MAP[off_id]
            return name
        return "Unknown"

    @property
    def song_author(self) -> Any:
        """返回歌曲作者的可读字符串，若无信息则返回 Unknown"""
        # 优先自定义歌曲
        if self.song_info:
            return self.song_info.get("artist_name")
        # 官方歌曲
        off_id = self.official_song
        # 部分关卡可能 custom_song_id 为 0 但官方歌曲空，需要检查
        if off_id is not None and off_id in OFFICIAL_SONG_MAP:
            _name, author = OFFICIAL_SONG_MAP[off_id]
            return author
        return "Unknown"

    def decrypt_password(self) -> Optional[str]:
        if not self.password:
            return None
        raw = base64.b64decode(self.password)
        key_bytes = self.XOR_KEY.encode("utf-8")
        decrypted = bytearray()
        for i, b in enumerate(raw):
            decrypted.append(b ^ key_bytes[i % len(key_bytes)])
        try:
            return decrypted.decode("utf-8")
        except UnicodeDecodeError:
            return decrypted.hex()

    def get_display_string(self) -> str:
        creator = self.creator_name or "Unknown"
        stars_str = f" - Stars:{self.stars}" if self.stars is not None else ""
        base = f"{self.level_name} by {creator} (ID:{self.level_id}){stars_str}"
        song_str = self._get_song_display()
        if song_str:
            base += f" [{song_str}]"
        return base

    def _get_song_display(self) -> Optional[str]:
        """返回歌曲的可读字符串，若无信息则返回 None"""
        # 优先自定义歌曲
        if self.song_info:
            s = self.song_info
            return f"{s.get('name')} by {s.get('artist_name')} (NG ID:{s.get('id')})"
        # 官方歌曲
        off_id = self.official_song
        custom_id = self.custom_song_id
        # 部分关卡可能 custom_song_id 为 0 但官方歌曲空，需要检查
        if off_id is not None and off_id in OFFICIAL_SONG_MAP:
            name, author = OFFICIAL_SONG_MAP[off_id]
            return f"{name} by {author} (Official)"
        # 兜底：有custom_song_id但未找到歌曲
        if custom_id and custom_id != 0:
            return f"Custom song (ID:{custom_id}) not loaded"
        return None

    def is_plat(self) -> bool:
        return int(self.length) == LENGTH_PLAT if self.length is not None else False

    def is_pemon(self) -> bool:
        return self.is_plat() and self.is_demon

    def is_demon_detail(self) -> bool:
        return self.is_demon and not self.is_plat()

    def difficulty_label(self) -> str:
        """获取该关卡的难度标识"""
        stars = int(self.stars) if self.stars is not None else None
        if stars is None:
            return "Unknown"
        sign = "🌙" if self.is_plat() else "⭐"

        if stars < DEMON_STARS:  # nondemon
            return [
                "Unrated",
                f"1{sign}auto",
                f"2{sign}easy",
                f"3{sign}normal",
                f"4{sign}hard",
                f"5{sign}hard",
                f"6{sign}harder",
                f"7{sign}harder",
                f"8{sign}insane",
                f"9{sign}insane",
            ][stars]
        if self.demon_difficulty is not None:
            return f"{['Hard', 'Unknown', 'Unknown', 'Easy', 'Medium', 'Insane', 'Extreme'][self.demon_difficulty]} {'Pemon' if self.is_pemon() else 'Demon'}"
            # bro what is rubtap doing it dont make sense
        return "10⭐demon"

    def __repr__(self) -> str:
        return f"<GDLevel {self.level_name!r} (ID:{self.level_id})>"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def parse_server_key_value_pairs(response: str) -> dict[int, str]:
    """Parse a RobTop-style key:value key:value response into a dict."""
    parts = response.split(":")
    result: dict[int, str] = {}
    i = 0
    while i < len(parts) - 1:
        key_str = parts[i]
        value_str = parts[i + 1]
        try:
            key = int(key_str)
        except ValueError:
            i += 1
            continue
        result[key] = value_str
        i += 2
    return result


def _parse_server_value(value: str, typ: Any) -> Any:
    if typ is int:
        try:
            return int(value) if value != "" else 0
        except ValueError:
            return value
    if typ is bool:
        return value == "1"
    if typ == "base64":
        value = value.replace("-", "+").replace("_", "/")
        if len(value) % 4 != 0:
            value += "=" * (4 - (len(value) % 4))
        try:
            return base64.b64decode(value).decode("utf-8")
        except Exception as e:  # noqa: BLE001
            logger.error("base64解码错误：%s for %s", e, value)
            return value
    if typ == "comma_int_list":
        if not value:
            return []
        return [int(item) for item in value.split(",") if item != ""]
    if typ is str:
        return unquote(value) if "%" in value else value
    return value


class GDUser:
    """Geometry Dash 用户数据类，解析服务器 key:value 响应。"""

    FIELD_MAP: Final = {
        1: ("user_name", str),
        2: ("user_id", int),
        3: ("stars", int),
        4: ("demons_count", int),
        6: ("ranking", int),
        7: ("account_highlight", int),
        8: ("creator_points", int),
        9: ("icon_id", int),
        10: ("color", int),
        11: ("color2", int),
        13: ("secret_coins", int),
        14: ("icon_type", int),
        15: ("special", int),
        16: ("account_id", int),
        17: ("user_coins", int),
        18: ("message_state", int),
        19: ("friends_state", int),
        20: ("youtube", str),
        21: ("acc_icon", int),
        22: ("acc_ship", int),
        23: ("acc_ball", int),
        24: ("acc_bird", int),
        25: ("acc_dart", int),
        26: ("acc_robot", int),
        27: ("acc_streak", int),
        28: ("acc_glow", int),
        29: ("is_registered", bool),
        30: ("global_rank", int),
        31: ("friend_state", int),
        38: ("messages", int),
        39: ("friend_requests", int),
        40: ("new_friends", int),
        41: ("new_friend_request", bool),
        42: ("age", str),
        43: ("acc_spider", int),
        44: ("twitter", str),
        45: ("twitch", str),
        46: ("diamonds", int),
        48: ("acc_explosion", int),
        49: ("modlevel", int),
        50: ("comment_history_state", int),
        51: ("color3", int),
        52: ("moons", int),
        53: ("acc_swing", int),
        54: ("acc_jetpack", int),
        55: ("demons_breakdown", "comma_int_list"),
        56: ("classic_levels", "comma_int_list"),
        57: ("platformer_levels", "comma_int_list"),
    }

    user_name: str
    user_id: int
    stars: int
    demons_count: int
    ranking: Optional[int] = None
    account_highlight: Optional[int] = None
    creator_points: int
    icon_id: Optional[int] = None
    color: int
    color2: int
    secret_coins: Optional[int] = None
    icon_type: int
    special: Optional[int] = None
    account_id: int
    user_coins: int
    message_state: Optional[int] = None
    friends_state: Optional[int] = None
    youtube: Optional[str] = None
    acc_icon: int
    acc_ship: int
    acc_ball: int
    acc_bird: int
    acc_dart: int
    acc_robot: int
    acc_streak: Optional[int] = None
    acc_glow: int
    is_registered: Optional[bool] = None
    global_rank: Optional[int] = None
    friend_state: Optional[int] = None
    messages: Optional[int] = None
    friend_requests: Optional[int] = None
    new_friends: Optional[int] = None
    new_friend_request: Optional[bool] = None
    age: Optional[str] = None
    acc_spider: int
    twitter: Optional[str] = None
    twitch: Optional[str] = None
    diamonds: int
    acc_explosion: Optional[int] = None
    modlevel: Optional[int] = None
    comment_history_state: Optional[int] = None
    color3: Optional[int] = None
    moons: int
    acc_swing: int
    acc_jetpack: int
    demons_breakdown: Optional[list[int]] = None
    classic_levels: Optional[list[int]] = None
    platformer_levels: Optional[list[int]] = None

    def __init__(self) -> None:
        for attr, _ in self.FIELD_MAP.values():
            setattr(self, attr, None)

    @classmethod
    def from_server_response(cls, response: str) -> "GDUser":
        """Parse a single user server response string into a GDUser."""
        instance = cls()
        pairs = parse_server_key_value_pairs(response)
        for key, (attr, typ) in cls.FIELD_MAP.items():
            if key not in pairs:
                continue
            setattr(instance, attr, _parse_server_value(pairs[key], typ))
        return instance

    @classmethod
    def from_string(cls, response: str) -> "GDUser":
        return cls.from_server_response(response)

    def __repr__(self) -> str:
        return f"<GDUser {self.user_name!r} (ID:{self.user_id})>"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def parse_song_object(song_str: str) -> Optional[dict[str, Any]]:
    tokens = song_str.split("~|~")
    song_data = {}
    needed = {
        1: "id",
        2: "name",
        3: "artist_id",
        4: "artist_name",
        5: "size",
        10: "link",
    }
    try:
        i = 0
        while i < len(tokens) - 1:
            key_str = tokens[i]
            value_str = tokens[i + 1]
            try:
                key = int(key_str)
            except ValueError:
                i += 1  # 跳过无法解析的部分
                continue
            if key in needed:
                attr = needed[key]
                if key in {1, 3}:
                    song_data[attr] = int(value_str) if value_str else 0
                elif key == 5:  # noqa: PLR2004
                    song_data[attr] = float(value_str) if value_str else 0.0
                elif key == 10:  # noqa: PLR2004
                    song_data[attr] = unquote(value_str) if value_str else ""
                else:
                    song_data[attr] = value_str
            i += 2
    except Exception as e:  # noqa: BLE001
        logger.warning("Song parse failed: %s", e)
        return None
    return song_data if "id" in song_data else None


@dataclass
class SearchPage:
    """一次 getGJLevels21 请求的结果，带上响应里的分页信息。

    分页信息来自响应的第 4 段（`total:offset:pagesize`），
    以前 search_levels 把这段直接丢掉了。
    """

    levels: list["GDLevel"] = field(default_factory=list)
    total: int = 0
    offset: int = 0
    page_size: int = GD_PAGE_SIZE
    page: int = 0

    @property
    def total_is_capped(self) -> bool:
        """total 是不是封顶值。

        是的话说明服务器没给真实条数，别拿它去算总页数或者显示「共 N 条」。
        """
        return self.total >= GD_TOTAL_CAP

    @property
    def is_empty(self) -> bool:
        return not self.levels


def search_levels_page(  # noqa: PLR0913
    query: Optional[str] = None,
    page: int = 0,
    **kwargs: Any,
) -> SearchPage:
    """和 search_levels 一样，但把分页信息一起返回。

    没有结果（服务器返回 -1）时返回一个空的 SearchPage，不抛异常 ——
    翻页翻过头和搜不到东西，服务器给的都是 -1。
    """
    return _search_levels(query=query, page=page, **kwargs)


def _search_levels(  # noqa: C901, PLR0912, PLR0913, PLR0915
    query: Optional[str] = None,
    page: int = 0,
    *,
    search_type: int = 0,
    diff: Optional[str] = None,
    demon_filter: Optional[int] = None,
    length: Optional[str] = None,
    featured: Optional[bool] = None,
    original: Optional[bool] = None,
    two_player: Optional[bool] = None,
    coins: Optional[bool] = None,
    epic: Optional[bool] = None,
    legendary: Optional[bool] = None,
    mythic: Optional[bool] = None,
    no_star: Optional[bool] = None,
    star: Optional[bool] = None,
    song: Optional[int] = None,
    custom_song: Optional[bool] = None,
    uncompleted: Optional[bool] = None,
    only_completed: Optional[bool] = None,
    completed_levels: Optional[str] = None,
    gauntlet: Optional[int] = None,
    local: Optional[bool] = None,
    account_id: Optional[int] = None,
    gjp2: Optional[str] = None,
    udid: Optional[str] = None,
    uuid: Optional[str] = None,
    game_version: int = 22,
    binary_version: int = 42,
    gdw: int = 0,
    **kwargs: Any,
) -> SearchPage:
    """i dumped every param so it looks like this lol"""
    url = "http://www.boomlings.com/database/getGJLevels21.php"
    headers = {"User-Agent": ""}

    data = {
        "secret": "Wmfd2893gb7",
        "gameVersion": game_version,
        "binaryVersion": binary_version,
        "type": search_type,
        "page": page,
        "gdw": gdw,
    }
    if query is not None:
        data["str"] = query

    def bool_param(name: str, val: Optional[bool]):  # noqa: FBT001
        if val is not None:
            data[name] = "1" if val else "0"

    bool_param("featured", featured)
    bool_param("original", original)
    bool_param("twoPlayer", two_player)
    bool_param("coins", coins)
    bool_param("epic", epic)
    bool_param("legendary", legendary)
    bool_param("mythic", mythic)
    bool_param("noStar", no_star)
    bool_param("star", star)
    bool_param("customSong", custom_song)
    bool_param("uncompleted", uncompleted)
    bool_param("onlyCompleted", only_completed)
    bool_param("local", local)

    optional = {
        "diff": diff,
        "len": length,
        "demonFilter": demon_filter,
        "song": song,
        "completedLevels": completed_levels,
        "gauntlet": gauntlet,
        "accountID": account_id,
        "gjp2": gjp2,
        "udid": udid,
        "uuid": uuid,
    }
    data.update({k: v for k, v in optional.items() if v is not None})
    data.update(kwargs)

    try:
        resp = requests.post(url, data=data, headers=headers, timeout=GD_TIMEOUT)
    except requests.RequestException as e:
        logger.error(f"[gdapi] 搜索请求失败: {e}")
        return SearchPage(page=page)
    text = resp.text.strip()
    # -1 有两种意思：搜不到东西，或者页码翻过头了。这里都当成空页返回，
    # 由调用方结合当前页码去区分。
    if text == "-1":
        return SearchPage(page=page)

    # 分割响应：关卡#作者#歌曲#分页信息#hash
    parts = text.split("#")
    if len(parts) < 4:  # noqa: PLR2004
        raise ValueError(f"响应格式不正确: {text}")  # noqa: TRY003

    levels_raw = parts[0]
    creators_raw = parts[1] if len(parts) > 1 else ""
    songs_raw = parts[2] if len(parts) > 2 else ""  # noqa: PLR2004

    # --- 分页信息：total:offset:pagesize ---
    total, offset, page_size = 0, page * GD_PAGE_SIZE, GD_PAGE_SIZE
    page_info = parts[3].split(":")
    if len(page_info) >= 3:  # noqa: PLR2004
        try:
            total, offset, page_size = (int(x) for x in page_info[:3])
        except ValueError:
            logger.warning(f"分页信息解析失败，按默认值处理: {parts[3]!r}")

    # --- 关卡列表 ---
    level_strs = [s for s in levels_raw.split("|") if s.strip()]
    levels = [GDLevel.from_server_response(s) for s in level_strs]

    # --- 创作者解析，使用 player_id 准确匹配 ---
    creator_map: dict[int, str] = {}
    if creators_raw:
        for entry in creators_raw.split("|"):
            if not entry.strip():
                continue
            parts_c = entry.split(":")
            if len(parts_c) >= 2:  # noqa: PLR2004
                try:
                    uid = int(parts_c[0])
                    uname = parts_c[1]
                    creator_map[uid] = uname
                except ValueError:
                    continue
    for level in levels:
        pid = level.player_id
        if pid is not None and pid in creator_map:
            level.creator_name = creator_map[pid]

    # --- 歌曲解析 ---
    song_entries = songs_raw.split("~:~") if songs_raw else []
    song_dict: dict[int, dict] = {}
    for s in song_entries:
        if not s.strip():
            continue
        info = parse_song_object(s)
        if info:
            song_dict[info["id"]] = info

    # 关联歌曲信息，优先 custom song，否则使用官方歌曲显示
    for level in levels:
        logger.debug(
            "Level %s (ID:%s) official_song=%s custom_song_id=%s",
            level.level_name,
            level.level_id,
            level.official_song,
            level.custom_song_id,
        )

        if level.custom_song_id and level.custom_song_id in song_dict:
            level.song_info = song_dict[level.custom_song_id]
        elif (
            level.official_song is not None and level.official_song in OFFICIAL_SONG_MAP
        ):
            level.song_info = None
        else:
            level.song_info = None
            if level.custom_song_id:
                logger.debug(
                    "关卡 %s (ID:%s) 的 custom_song_id=%s 未在歌曲列表中找到",
                    level.level_name,
                    level.level_id,
                    level.custom_song_id,
                )

    return SearchPage(
        levels=levels,
        total=total,
        offset=offset,
        page_size=page_size or GD_PAGE_SIZE,
        page=page,
    )


def search_levels(
    query: Optional[str] = None,
    page: int = 0,
    **kwargs: Any,
) -> list[GDLevel]:
    """按条件搜索关卡，只返回关卡列表。

    要拿分页信息（总数 / 偏移）用 search_levels_page。
    """
    return _search_levels(query=query, page=page, **kwargs).levels

def get_user_info(
    user_id: int
) -> Optional[GDUser]:
    url = "http://www.boomlings.com/database/getGJUserInfo20.php"
    headers = {"User-Agent": ""}

    data = {
        "secret": "Wmfd2893gb7",
        "targetAccountID": str(user_id)
    }
    try:
        resp = requests.post(url, data=data, headers=headers, timeout=GD_TIMEOUT)
    except requests.RequestException as e:
        logger.error(f"[gdapi] get_user_info({user_id}) 请求失败: {e}")
        return None
    text = resp.text.strip()
    logger.info(f"get_user_info({user_id}): {text}")
    if text == "-1":
        return None
    return GDUser.from_server_response(text.split("#")[0])

def search_user(
    name: str
) -> Optional[GDUser]:
    url = "http://www.boomlings.com/database/getGJUsers20.php"
    headers = {"User-Agent": ""}

    data = {
        "secret": "Wmfd2893gb7",
        "str": name
    }
    try:
        resp = requests.post(url, data=data, headers=headers, timeout=GD_TIMEOUT)
    except requests.RequestException as e:
        logger.error(f"[gdapi] search_user({name}) 请求失败: {e}")
        return None
    text = resp.text.strip()
    logger.info(f"search_user({name}): {text}")
    if text == "-1":
        return None
    return GDUser.from_server_response(text.split("#")[0])


from typing import Optional


def search_levels_by_name(  # noqa: PLR0913
    name: str,
    page: int = 0,
    *,
    diff: Optional[str] = None,
    demon_filter: Optional[int] = None,
    length: Optional[str] = None,
    featured: Optional[bool] = None,
    epic: Optional[bool] = None,
    legendary: Optional[bool] = None,
    mythic: Optional[bool] = None,
    star: Optional[bool] = None,
    **kwargs: Any,
) -> list[GDLevel]:
    """
    按关卡名称搜索，返回 GDLevel 对象列表。

    参数：
        name (str): 搜索关键词。
        page (int): 页码，默认 0。
        diff (int): 难度筛选。-3=auto, -1=未评级, 1~5=easy/normal/hard/harder/insane,
            -2=demon（要配合 demon_filter 细分）。筛的是关卡自报难度（响应字段 9），
            所以未评级的关卡也能筛到。
        demon_filter (int): 恶魔难度筛选，**1=easy, 2=medium, 3=hard, 4=insane, 5=extreme**，
            0 等于不筛。注意这套刻度和响应里的字段 43 不一样（字段 43 是
            0=hard, 3=easy, 4=medium, 5=insane, 6=extreme），别搞混 ——
            按字段 43 的刻度传 6 进来服务器会一条都不返回。
        length (str): 长度筛选，如 "tiny", "short", "medium", "long", "xl"。
        featured (bool), epic (bool), legendary (bool), mythic (bool): self-explain
        star (bool): if it is rated
        **kwargs: 其他可选参数，会透传给 search_levels。

    返回：
        List[GDLevel]: 匹配的关卡列表。
    """
    return search_levels(
        query=name,
        page=page,
        search_type=0,  # 固定为关键词搜索
        diff=diff,
        demon_filter=demon_filter,
        length=length,
        featured=featured,
        epic=epic,
        legendary=legendary,
        mythic=mythic,
        star=star,
        **kwargs,
    )

OFFICIAL_LEVELS = {
    1:GDLevel.from_server_response("1:1:2:Clubstep:3:VGhpcyBpcyB0aGUgZmlyc3Qgb2ZmaWNpYWwgZGVtb24gYW5kIG15IGZpcnN0IGRlbW9uIGFuZCBJIGhhdmUgdG8gY29uc3RydWN0IGEgZmFrZSBsZXZlbCBkYXRhIGZvciB0aGlzIHByb2dyYW0gdG8gcmVhZCB0byBwcmV2ZW50IGV2ZXJ5dGhpbmcgdG8gZ28gd3Jvbmc=:12:13:15:3:17:1:18:10:43:1:"),
    2:GDLevel.from_server_response("1:2:2:Theory of everything 2:3:VGhpcyBpcyB0aGUgc2Vjb25kIGFuZCBtb3N0IGZvcmdldGFibGUgb2ZmaWNpYWwgZGVtb24gYW5kIEkgaGF2ZSB0byBjb25zdHJ1Y3QgYSBmYWtlIGxldmVsIGRhdGEgZm9yIHRoaXMgcHJvZ3JhbSB0byByZWFkIHRvIHByZXZlbnQgZXZlcnl0aGluZyB0byBnbyB3cm9uZw==:12:17:15:3:17:1:18:10:43:1:"),  # noqa: E501
    3:GDLevel.from_server_response("1:3:2:Deadlocked:3:VGhpcyBpcyB0aGUgdGhpcmQgYW5kIGZpbmFsIG9mZmljaWFsIGRlbW9uIGFuZCBJIGhhdmUgdG8gY29uc3RydWN0IGEgZmFrZSBsZXZlbCBkYXRhIGZvciB0aGlzIHByb2dyYW0gdG8gcmVhZCB0byBwcmV2ZW50IGV2ZXJ5dGhpbmcgdG8gZ28gd3Jvbmc=:12:19:15:3:17:1:18:10:43:1:"),
}

def get_level_by_id(level_id: int) -> Optional[GDLevel]:
    """通过关卡 ID 获取单个关卡对象。"""
    if level_id in OFFICIAL_LEVELS:
        return OFFICIAL_LEVELS[level_id]
    results = search_levels(
        query=str(level_id),
    )
    if results:
        return results[0]
    return None

def get_user_by_name(user_name: str) -> Optional[GDUser]:
    """通过用户名获取单个用户对象。"""
    search_result = search_user(user_name)
    if search_result:
        return get_user_info(search_result.account_id)
    return None
