from typing import Any, Optional, Union

import requests
from nonebot import logger

apikey = "3244ce47ed4cf932ec348d68cdf72496de68ee48a2846044db906baa28a7cf7d"
HTTP_OK = 200
GDDL_PLAT_LENGTH = 6

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
class Gddl:
    @staticmethod
    def getleveltags(level_id: Union[str, int]) -> list[dict[str, Any]]:
        """??????gddl api?????????????????????tag"""
        url = f"https://gdladder.com/api/level/{level_id}/tags"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}",
        }
        response = requests.get(url, headers=headers)
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
            response = requests.get(url, headers=headers, params=data)
            if response.status_code == HTTP_OK:
                data = response.json()
                return [GDDLLevel(level_data) for level_data in data["levels"]]
        except requests.RequestException as e:
            logger.error(f"Error fetching levels: {e}")
        return []

    @staticmethod
    def getlevelbyid(level_id: Union[str, int]) -> Optional[GDDLLevel]:
        """??????gddl api????????????id????????????????????????"""
        url = f"https://gdladder.com/api/level/{level_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}",
        }
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == HTTP_OK:
                data = response.json()
                tags = Gddl.getleveltags(level_id)
                return GDDLLevel(data, tags)
        except requests.RequestException as e:
            logger.error(f"Error fetching level by ID: {e}")
            return None
        return None

    @staticmethod
    def getrandomlevelbytier(low: int, high: int = -1) -> Optional[GDDLLevel]:
        """??????gddl??????api???????????????????????????????????????"""
        if high == -1:
            high = low
        high_exact = min(high + 0.5, 39.0)
        low_exact = max(low - 0.5, 1.0)
        url = "https://gdladder.com/api/level/search"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}",
        }
        data = {"minRating": low_exact, "maxRating": high_exact, "sort": "random"}
        try:
            response = requests.get(url, headers=headers, params=data)
            if response.status_code == HTTP_OK:
                data = response.json()
                logger.debug(f"?????????{len(data['levels'])}????????????{','.join(level['Meta']['Name'] for level in data['levels'])}")
                if len(data["levels"]) > 0:
                    return GDDLLevel(data["levels"][0])
                return None
            logger.warning(f"Error fetching levels: {response.status_code}")
        except requests.RequestException as e:
            logger.error(f"Error fetching levels: {e}")
        return None
