from typing import Any, Optional, Union

import requests
from nonebot import logger

apikey = "3244ce47ed4cf932ec348d68cdf72496de68ee48a2846044db906baa28a7cf7d"
HTTP_OK = 200
GDDL_PLAT_LENGTH = 6
# 提交评分一页放几条。网页上是 10 条一页，跟着来。
GDDL_SUBMISSION_LIMIT = 10
# gdladder 的请求超时
GDDL_TIMEOUT = 15
# 接口限制 limit 只能 1-30，超了直接 400
GDDL_LIMIT_MIN = 1
GDDL_LIMIT_MAX = 30
# 排序字段，照 API 文档的 SubmissionSortOptions
SUBMISSION_SORTS = frozenset(
    {"attempts", "dateAdded", "enjoyment", "rating", "progress", "refreshRate", "username"}
)
# 方向只认小写的 asc/desc，传 ASC/DESC 会 400
SORT_DIRECTIONS = frozenset({"asc", "desc"})
PROGRESS_FILTERS = frozenset({"all", "victors", "incomplete"})

"""
SongDTO{
ID*	integer
Name*	string
Author*	string
Size*	number
}
"""
class SongInfo:
    ID: int
    Name: str
    Author: str

    def __init__(self, jsondict: dict[str, Any]) -> None:
        self.ID = jsondict["ID"]
        self.Name = jsondict["Name"]
        self.Author = jsondict["Author"]

    def __str__(self) -> str:
        return f"ID: {self.ID}\nName: {self.Name}\nAuthor: {self.Author}"

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

"""
LevelMetaDTO{
ID*	integer
Name*	string
Description*	string | null
SongID*	integer Negative IDs are main songs.
Length*	number Enum: [ 1, 2, 3, 4, 5, 6 ] # 6=plat
IsTwoPlayer*	boolean
Difficulty*	string Enum: [ Official, Easy, Medium, Hard, Insane, Extreme ]
PublisherID*	integer # internal ID that we don't use
UploadedAt*	string | null
Song SongDTO
}
"""
class LevelMeta:
    ID: int
    Name: str
    Description: Optional[str] = None
    SongID: int
    Length: int #[1, 2, 3, 4, 5, 6] for tiny, short, medium, long, XL, plat
    IsTwoPlayer: bool
    Difficulty: str #[Official, Easy, Medium, Hard, Insane, Extreme]
    PublisherID: int
    UploadedAt: Optional[str] = None
    Song: SongInfo

    def __init__(self, jsondict: dict[str, Any]) -> None:
        self.ID = jsondict["ID"]
        self.Name = jsondict["Name"]
        self.Description = jsondict["Description"]
        self.SongID = jsondict["SongID"]
        self.Length = jsondict["Length"]
        self.IsTwoPlayer = jsondict["IsTwoPlayer"]
        self.Difficulty = jsondict["Difficulty"]
        self.Song = SongInfo(jsondict["Song"])

    def is_pemon(self) -> bool:
        return self.Length == GDDL_PLAT_LENGTH

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

"""
LevelDTO{
ID*	integer
Rating*	number | null
Enjoyment*	number | null
Deviation*	number | null
RatingCount*	integer
EnjoymentCount*	integer
SubmissionCount*	integer
TwoPlayerRating*	number | null
TwoPlayerEnjoyment*	number | null
TwoPlayerDeviation*	number | null
DefaultRating*	integer | null      # A tier rating set by staff.
Showcase*	string | null           # A YouTube video ID.
Popularity*	number | null           # use it to compare levels.
Meta	LevelMetaDTO
}
"""
class GDDLLevel:
    ID: int
    Rating: Optional[float] = None
    Enjoyment: Optional[float] = None
    Deviation: Optional[float] = None
    RatingCount: int
    EnjoymentCount: int
    SubmissionCount: int
    TwoPlayerRating: Optional[float] = None
    TwoPlayerEnjoyment: Optional[float] = None
    TwoPlayerDeviation: Optional[float] = None
    DefaultRating: Optional[int] = None
    Showcase: Optional[str] = None
    Meta: LevelMeta
    Tags: list[dict[str,str]]

    def __init__(self, jsondict: dict[str, Any], tags: Optional[list[dict[str, str]]] = None) -> None:
        if tags is None:
            tags = []
        self.ID = jsondict["ID"]
        self.Rating = jsondict["Rating"]
        self.Enjoyment = jsondict["Enjoyment"]
        self.Deviation = jsondict["Deviation"]
        self.RatingCount = jsondict["RatingCount"]
        self.EnjoymentCount = jsondict["EnjoymentCount"]
        self.SubmissionCount = jsondict["SubmissionCount"]
        self.TwoPlayerRating = jsondict["TwoPlayerRating"]
        self.TwoPlayerEnjoyment = jsondict["TwoPlayerEnjoyment"]
        self.TwoPlayerDeviation = jsondict["TwoPlayerDeviation"]
        self.DefaultRating = jsondict["DefaultRating"]
        self.Showcase = jsondict["Showcase"]
        self.Meta = LevelMeta(jsondict["Meta"])
        self.Tags = tags or []

    def is_pemon(self) -> bool:
        return self.Meta.is_pemon()

"""
[
GetLevelTagsResponseDTO{
TagID*	integer
ReactCount*	integer
HasVoted*	integer in [0,1]
Tag*	TagDTO{
        ID*	integer
        Name*	string
        Description*	string
        Ordering*	integer
        }
}
]
"""
class Submission:
    """GDDL 上某个人对某关卡提交的一条评分。

    对应 API 文档里的 SubmissionDTO。Rating 是 tier（1-39），
    Enjoyment 是 0-10，两个都可能是 null（只填了其中一项）。
    """

    def __init__(self, jsondict: dict[str, Any]) -> None:
        self.id = jsondict.get("ID")
        self.rating = jsondict.get("Rating")          # tier，可能为 None
        self.enjoyment = jsondict.get("Enjoyment")    # 0-10，可能为 None
        self.refresh_rate = jsondict.get("RefreshRate")
        self.device = jsondict.get("Device")
        self.proof = jsondict.get("Proof")
        self.is_solo = jsondict.get("IsSolo", True)
        self.progress = jsondict.get("Progress")
        self.attempts = jsondict.get("Attempts")
        self.date_added = jsondict.get("DateAdded")
        user = jsondict.get("User") or {}
        self.user_id = jsondict.get("UserID")
        self.user_name = user.get("Name")
        second = jsondict.get("SecondaryUser") or {}
        self.second_user_name = second.get("Name")

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class SubmissionPage:
    """/api/level/{id}/submissions 的一页结果"""

    def __init__(self, jsondict: dict[str, Any]) -> None:
        self.total: int = jsondict.get("total", 0)
        self.limit: int = jsondict.get("limit", GDDL_SUBMISSION_LIMIT)
        self.page: int = jsondict.get("page", 0)
        self.submissions: list[Submission] = [
            Submission(s) for s in jsondict.get("submissions", [])
        ]

    @property
    def total_pages(self) -> int:
        if self.limit <= 0:
            return 1
        return max(1, -(-self.total // self.limit))  # 向上取整


class Gddl:
    @staticmethod
    def getsubmissions(  # noqa: PLR0913
        level_id: Union[str, int],
        page: int = 0,
        limit: int = GDDL_SUBMISSION_LIMIT,
        sort: Optional[str] = None,
        sort_direction: Optional[str] = None,
        progress_filter: Optional[str] = None,
    ) -> Optional[SubmissionPage]:
        """拿某关卡的提交评分列表（就是网页上「Submitted ratings」那块）。

        page 从 0 开始，limit 只能 1-30。
        sort 见 SUBMISSION_SORTS，sort_direction 只认小写 asc/desc
        （传 ASC/DESC 接口直接 400，实测过）。
        progress_filter 见 PROGRESS_FILTERS。
        非法的值这里直接丢掉不传，免得整个请求被打回来。
        请求失败返回 None（和「没有提交」区分开）。
        """
        url = f"https://gdladder.com/api/level/{level_id}/submissions"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}",
        }
        params: dict[str, Any] = {
            "page": max(0, page),
            "limit": min(GDDL_LIMIT_MAX, max(GDDL_LIMIT_MIN, limit)),
        }
        if sort:
            if sort in SUBMISSION_SORTS:
                params["sort"] = sort
            else:
                logger.warning(f"[gddl] 不认识的排序字段 {sort!r}，忽略")
        if sort_direction:
            direction = sort_direction.lower()
            if direction in SORT_DIRECTIONS:
                params["sortDirection"] = direction
            else:
                logger.warning(f"[gddl] 不认识的排序方向 {sort_direction!r}，忽略")
        if progress_filter:
            if progress_filter in PROGRESS_FILTERS:
                params["progressFilter"] = progress_filter
            else:
                logger.warning(f"[gddl] 不认识的进度过滤 {progress_filter!r}，忽略")
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
        except requests.RequestException as e:
            logger.error(f"[gddl] 拉取提交评分失败 level={level_id}: {e}")
            return None
        if response.status_code != HTTP_OK:
            logger.warning(
                f"[gddl] 提交评分接口返回 {response.status_code} level={level_id}"
            )
            return None
        return SubmissionPage(response.json())

    @staticmethod
    def getspread(level_id: Union[str, int]) -> Optional[dict[str, Any]]:
        """拿某关卡的 tier / enjoyment 分布直方图"""
        url = f"https://gdladder.com/api/level/{level_id}/submissions/spread"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}",
        }
        try:
            response = requests.get(url, headers=headers, timeout=15)
        except requests.RequestException as e:
            logger.error(f"[gddl] 拉取分布失败 level={level_id}: {e}")
            return None
        if response.status_code != HTTP_OK:
            return None
        return response.json()

    @staticmethod
    def getleveltags(level_id: Union[str, int]) -> list[dict[str, Any]]:
        """??????gddl api?????????????????????tag"""
        url = f"https://gdladder.com/api/level/{level_id}/tags"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}",
        }
        try:
            response = requests.get(url, headers=headers, timeout=GDDL_TIMEOUT)
        except requests.RequestException as e:
            logger.error(f"[gddl] 拉 tags 失败 level={level_id}: {e}")
            return []
        if response.status_code == HTTP_OK:
            data = response.json()
            return [
                {"Name": tag["Tag"]["Name"], "Count": tag["ReactCount"]} for tag in data
            ]
        logger.error(f"Error fetching level tags by ID: {level_id}")
        return []

    @staticmethod
    def getlevelsbyname(name: str) -> list[GDDLLevel]:
        """??????gddl api????????????????????????????????????????????????"""
        url = "https://gdladder.com/api/level/search"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}",
        }
        data = {"name": name}
        try:
            response = requests.get(url, headers=headers, params=data, timeout=GDDL_TIMEOUT)
            if response.status_code == HTTP_OK:
                data = response.json()
                return [GDDLLevel(level_data) for level_data in data["levels"]]
        except requests.RequestException as e:
            logger.error(f"Error fetching levels: {e}")
        return []

    @staticmethod
    def getlevelbyid(
        level_id: Union[str, int],
        with_tags: bool = True,  # noqa: FBT001, FBT002
    ) -> Optional[GDDLLevel]:
        """??????gddl api????????????id????????????????????????"""
        url = f"https://gdladder.com/api/level/{level_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}",
        }
        try:
            response = requests.get(url, headers=headers, timeout=GDDL_TIMEOUT)
            if response.status_code == HTTP_OK:
                data = response.json()
                # with_tags=False 时不在这里顺带拉 tags —— 那是第二次往返，
                # 调用方想并发的话可以自己单独调 getleveltags
                tags = Gddl.getleveltags(level_id) if with_tags else None
                return GDDLLevel(data, tags)
        except requests.RequestException as e:
            logger.error(f"Error fetching level by ID: {e}")
            return None
        return None

    @staticmethod
    def searchlevels(
        page: int = 0,
        limit: int = 1,
        sort: str = "ID",
        **filters: Any,
    ) -> Optional[dict[str, Any]]:
        """按条件搜 GDDL，返回原始响应（带 total / limit / page / levels）。

        filters 直接透传给接口，常用的有 minRating / maxRating（1-39）、
        minEnjoyment / maxEnjoyment（0-10）、minSubmissionCount。
        请求失败返回 None。
        """
        url = "https://gdladder.com/api/level/search"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}",
        }
        params: dict[str, Any] = {"page": max(0, page), "limit": limit, "sort": sort}
        params.update({k: v for k, v in filters.items() if v is not None})
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
        except requests.RequestException as e:
            logger.error(f"[gddl] 搜索失败: {e}")
            return None
        if response.status_code != HTTP_OK:
            logger.warning(f"[gddl] 搜索接口返回 {response.status_code}，参数 {params}")
            return None
        return response.json()

    @staticmethod
    def getlevelbyindex(index: int, **filters: Any) -> Optional[GDDLLevel]:
        """按 ID 升序取符合条件的第 index 个关卡（从 0 开始）。

        用 sort=ID 而不是 sort=random，这样同样的 index 每次拿到的都是同一关，
        *dailydemon 就是靠这个做到一天之内结果不变的。
        """
        payload = Gddl.searchlevels(page=index, limit=1, sort="ID", **filters)
        if not payload or not payload.get("levels"):
            return None
        return GDDLLevel(payload["levels"][0])

    @staticmethod
    def getrandomlevelbytier(
        low: int,
        high: int = -1,
        enjoyment_min: Optional[float] = None,
        enjoyment_max: Optional[float] = None,
    ) -> Optional[GDDLLevel]:
        """在指定 tier 区间里随机取一关，可以再按 enjoyment 卡一道。

        tier 用 ±0.5 展开成区间，这样传 20 能把 19.5-20.5 的都算进去。
        enjoyment 是 0-10，不传就不筛。
        """
        if high == -1:
            high = low
        high_exact = min(high + 0.5, 39.0)
        low_exact = max(low - 0.5, 1.0)

        payload = Gddl.searchlevels(
            page=0,
            limit=1,
            sort="random",
            minRating=low_exact,
            maxRating=high_exact,
            minEnjoyment=enjoyment_min,
            maxEnjoyment=enjoyment_max,
        )
        if not payload:
            return None
        levels = payload.get("levels") or []
        logger.debug(
            f"[gddl] 随机推关命中 {payload.get('total')} 条，本次取 "
            + ",".join(lv["Meta"]["Name"] for lv in levels)
        )
        if not levels:
            return None
        return GDDLLevel(levels[0])
