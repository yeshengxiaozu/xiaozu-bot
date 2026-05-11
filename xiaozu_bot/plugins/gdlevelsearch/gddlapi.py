from nonebot import logger
import requests
import json

apikey="3244ce47ed4cf932ec348d68cdf72496de68ee48a2846044db906baa28a7cf7d"

class Song:
    def __init__(self, ID, Name, Author):
        self.ID = ID
        self.Name = Name
        self.Author = Author
    def __init__(self, jsondict):
        self.ID = jsondict['ID']
        self.Name = jsondict['Name']
        self.Author = jsondict['Author']
    def __str__(self):
        return f"ID: {self.ID}\nName: {self.Name}\nAuthor: {self.Author}"

class LevelMeta:
    def __init__(self, ID, Name, Description, SongID, Length, IsTwoPlayer, Difficulty, PublisherID, UploadedAt, Song):
        self.ID = ID
        self.Name = Name
        self.Description = Description
        self.SongID = SongID
        self.Length = Length
        self.IsTwoPlayer = IsTwoPlayer
        self.Difficulty = Difficulty
        self.PublisherID = PublisherID
        self.UploadedAt = UploadedAt
        self.Song = Song
    def __init__(self, jsondict):
        self.ID = jsondict['ID']
        self.Name = jsondict['Name']
        self.Description = jsondict['Description']
        self.SongID = jsondict['SongID']
        self.Length = jsondict['Length']
        self.IsTwoPlayer = jsondict['IsTwoPlayer']
        self.Difficulty = jsondict['Difficulty']
        self.Song = Song(jsondict['Song'])
    def __str__(self):
        return f"ID: {self.ID}\nName: {self.Name}\nDescription: {self.Description}\nSongID: {self.SongID}\nLength: {self.Length}\nIsTwoPlayer: {self.IsTwoPlayer}\nDifficulty: {self.Difficulty}\nSong: {self.Song}"

class GDDLLevel:
    def __init__(self, ID, Rating, Enjoyment, Deviation, RatingCount, EnjoymentCount, SubmissionCount, TwoPlayerRating, TwoPlayerEnjoyment, TwoPlayerDeviation, DefaultRating, Showcase, Meta):
        self.ID = ID
        self.Rating = Rating
        self.Enjoyment = Enjoyment
        self.Deviation = Deviation
        self.RatingCount = RatingCount
        self.EnjoymentCount = EnjoymentCount
        self.SubmissionCount = SubmissionCount
        self.TwoPlayerRating = TwoPlayerRating
        self.TwoPlayerEnjoyment = TwoPlayerEnjoyment
        self.TwoPlayerDeviation = TwoPlayerDeviation
        self.DefaultRating = DefaultRating
        self.Showcase = Showcase
        self.Meta = Meta
    def __init__(self, jsondict):
        self.ID = jsondict['ID']
        self.Rating = jsondict['Rating']
        self.Enjoyment = jsondict['Enjoyment']
        self.Deviation = jsondict['Deviation']
        self.RatingCount = jsondict['RatingCount']
        self.EnjoymentCount = jsondict['EnjoymentCount']
        self.SubmissionCount = jsondict['SubmissionCount']
        self.TwoPlayerRating = jsondict['TwoPlayerRating']
        self.TwoPlayerEnjoyment = jsondict['TwoPlayerEnjoyment']
        self.TwoPlayerDeviation = jsondict['TwoPlayerDeviation']
        self.DefaultRating = jsondict['DefaultRating']
        self.Showcase = jsondict['Showcase']
        self.Meta = LevelMeta(jsondict['Meta'])
    def __str__(self):
        return f"ID: {self.ID}\nRating: {self.Rating}\nEnjoyment: {self.Enjoyment}\nDeviation: {self.Deviation}\nRatingCount: {self.RatingCount}\nEnjoymentCount: {self.EnjoymentCount}\nSubmissionCount: {self.SubmissionCount}\nTwoPlayerRating: {self.TwoPlayerRating}\nTwoPlayerEnjoyment: {self.TwoPlayerEnjoyment}\nTwoPlayerDeviation: {self.TwoPlayerDeviation}\nDefaultRating: {self.DefaultRating}\nShowcase: {self.Showcase}\nMeta:\n{str(self.Meta)}"

class gddl:
    def getlevelsbyname(name):
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
            if response.status_code == 200:
                data = response.json()
                return [GDDLLevel(level_data) for level_data in data["levels"]]
            else:
                return None
        except requests.RequestException as e:
            logger.error(f"Error fetching levels: {e}")
            return None
        
    def getlevelbyid(id):
        url = f"https://gdladder.com/api/level/{id}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}"
        }
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return GDDLLevel(data)
            else:
                return None
        except requests.RequestException as e:
            logger.error(f"Error fetching level by ID: {e}")
            return None
        
    def getleveltags(id):
        url = f"https://gdladder.com/api/level/{id}/tags"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {apikey}"
        }   
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return [{"Name": tag['Tag']['Name'], "Count": tag['ReactCount']} for tag in data]
        else:
            return []