import base64
import logging
from typing import Any, Final, Optional
from urllib.parse import unquote

import requests

# 配置日志，方便调试（若不需要可注释掉）
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEMON_STARS = 10
LENGTH_PLAT = 5

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
        26: ("record_string", str),
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

    level_id: int = 0
    level_name: str = ""
    description: Optional[str] = None
    level_string: Optional[str] = None
    version: Optional[int] = None
    player_id: Optional[int] = None
    difficulty_denominator: Optional[int] = None
    difficulty_numerator: Optional[int] = None
    downloads: Optional[int] = None
    official_song: Optional[int] = None
    game_version: Optional[int] = None
    likes: Optional[int] = None
    length: Optional[int] = None
    is_demon: Optional[bool] = None
    stars: int = 0
    feature_score: Optional[int] = None
    is_auto: Optional[bool] = None
    record_string: Optional[str] = None
    password: Optional[str] = None
    upload_date: Optional[str] = None
    update_date: Optional[str] = None
    copied_id: Optional[int] = None
    is_two_player: Optional[bool] = None
    custom_song_id: Optional[int] = None
    extra_string: Optional[str] = None
    coins: Optional[int] = None
    verified_coins: Optional[bool] = None
    stars_requested: Optional[int] = None
    low_detail_mode: Optional[bool] = None
    daily_number: Optional[int] = None
    epic: Optional[int] = None
    demon_difficulty: Optional[int] = None
    is_gauntlet: Optional[bool] = None
    objects: Optional[int] = None
    editor_time: Optional[int] = None
    editor_time_copies: Optional[int] = None
    settings_string: Optional[str] = None
    song_ids: Optional[str] = None
    sfx_ids: Optional[str] = None
    unknown54: Optional[int] = None
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
        parts = response.split(":")
        i = 0
        while i < len(parts) - 1:
            key_str = parts[i]
            value_str = parts[i + 1]
            try:
                key = int(key_str)
            except ValueError:
                i += 1
                continue
            if key == 4 and value_str == "{levelString}":  # noqa: PLR2004
                value_str = "{levelString}"
            if key in cls.FIELD_MAP:
                attr, typ = cls.FIELD_MAP[key]
                processed_value: Any = value_str
                if typ is int:
                    try:
                        processed_value = int(value_str) if value_str != "" else 0
                    except ValueError:
                        processed_value = value_str
                elif typ is bool:
                    processed_value = value_str == "1"
                elif typ == "base64":
                    try:
                        processed_value = base64.b64decode(value_str).decode("utf-8")
                    except Exception:  # noqa: BLE001
                        processed_value = value_str
                setattr(instance, attr, processed_value)
            i += 2
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

    def is_pemon(self) -> bool:
        return int(self.length) == LENGTH_PLAT if self.length is not None else False

    def is_demon_detail(self) -> bool:
        return self.stars == DEMON_STARS and not self.is_pemon()

    def difficulty_label(self) -> str:
        stars = int(self.stars) if self.stars is not None else None
        if stars is None:
            return "Unknown"
        sign = "🌙" if self.is_pemon() else "⭐"

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


def search_levels(  # noqa: C901, PLR0912, PLR0913, PLR0915
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
) -> list[GDLevel]:
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

    resp = requests.post(url, data=data, headers=headers)
    text = resp.text.strip()
    if text == "-1":
        return []

    # 分割响应
    parts = text.split("#")
    if len(parts) < 4:  # noqa: PLR2004
        raise ValueError(f"响应格式不正确: {text}")  # noqa: TRY003

    levels_raw = parts[0]
    creators_raw = parts[1] if len(parts) > 1 else ""
    songs_raw = parts[2] if len(parts) > 2 else ""  # noqa: PLR2004

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

    return levels


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
        diff (str): 难度筛选，如 "easy", "normal" 等，详见文档。
        demon_filter (int): 恶魔难度筛选，0=hard, 3=easy, 4=medium, 5=insane, 6=extreme。
        length (str): 长度筛选，如 "tiny", "short", "medium", "long", "xl"。
        featured (bool): 是否精选。
        epic (bool): 是否史诗。
        legendary (bool): 是否传说。
        mythic (bool): 是否神话。
        star (bool): 是否有星（评级）。
        **kwargs: 其他可选参数，会透传给 search_levels。

    返回：
        List[GDLevel]: 匹配的关卡列表。
    """
    return search_levels(
        query=name,
        page=page,
        type=0,  # 固定为关键词搜索
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


def get_level_by_id(level_id: int) -> Optional[GDLevel]:
    """
    通过关卡 ID 获取单个关卡对象。

    参数：
        level_id (int): 关卡 ID。

    返回：
        Optional[GDLevel]: 若找到返回关卡对象，否则返回 None。
    """
    # 使用 type=10 按 ID 列表查询，str 为逗号分隔的单 ID
    results = search_levels(
        query=str(level_id),
    )
    if results:
        return results[0]
    return None
