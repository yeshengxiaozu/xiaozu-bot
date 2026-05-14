from typing import Any, Optional, Union

import requests
from nonebot import logger

apikey="3244ce47ed4cf932ec348d68cdf72496de68ee48a2846044db906baa28a7cf7d"
HTTP_OK = 200
GDDL_PLAT_LENGTH = 6

class SongInfo:
    ID : int
    Name : str
    Author : str
    def __init__(self, jsondict: dict[str, Any]) -> None:
        self.ID = jsondict["ID"]
        self.Name = jsondict["Name"]
        self.Author = jsondict["Author"]
    def __str__(self) -> str:
        return f"ID: {self.ID}\nName: {self.Name}\nAuthor: {self.Author}"

class LevelMeta:
    ID : int
    Name : str
    Description : Optional[str] = None
    SongID : Optional[int] = None
    Length : int
    IsTwoPlayer : bool
    Difficulty : str
    PublisherID : int
    UploadedAt : Optional[str] = None
    Song : SongInfo

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

class GDDLLevel:
    ID : int
    Rating : Optional[float] = None
    Enjoyment : Optional[float] = None
    Deviation : Optional[float] = None
    RatingCount : Optional[int] = None
    EnjoymentCount : Optional[int] = None
    SubmissionCount : Optional[int] = None
    TwoPlayerRating : Optional[float] = None
    TwoPlayerEnjoyment : Optional[float] = None
    TwoPlayerDeviation : Optional[float] = None
    DefaultRating : Optional[int] = None
    Showcase : Optional[str] = None
    Meta : LevelMeta
    def __init__(self, jsondict: dict[str, Any]) -> None:
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
    def is_pemon(self) -> bool:
        return self.Meta.is_pemon()

class Gddl:
    @staticmethod
    def getlevelsbyname(name:str) -> list[GDDLLevel]:
        url = "https://gdladder.com/api/level/search"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}"
        }
        data = {
            "name": name
        }
        try:
            response = requests.get(url, headers=headers, params=data)
            if response.status_code == HTTP_OK:
                data = response.json()
                return [GDDLLevel(level_data) for level_data in data["levels"]]
        except requests.RequestException as e:
            logger.error(f"Error fetching levels: {e}")
        return []

    @staticmethod
    def getlevelbyid(level_id: Union[str,int]) -> Optional[GDDLLevel]:
        url = f"https://gdladder.com/api/level/{level_id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}"
        }
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == HTTP_OK:
                data = response.json()
                return GDDLLevel(data)
        except requests.RequestException as e:
            logger.error(f"Error fetching level by ID: {e}")
            return None
        return None

    @staticmethod
    def getleveltags(level_id: Union[str,Any]) -> list[dict[str, Any]]:
        url = f"https://gdladder.com/api/level/{level_id}/tags"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}"
        }
        response = requests.get(url, headers=headers)
        if response.status_code == HTTP_OK:
            data = response.json()
            return [{"Name": tag["Tag"]["Name"], "Count": tag["ReactCount"]} for tag in data]
        return []
