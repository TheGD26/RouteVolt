import os

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["OCM_API_KEY"]


url = "https://api.openchargemap.io/v3/poi/"


params = {
    "output":"json",
    "countrycode":"IN",
    "maxresults":100,
    "latitude":13.0827,
    "longitude":80.2707,
    "distance":100,
    "key":API_KEY
}


response = requests.get(url, params=params)

data = response.json()


stations=[]


for station in data:

    stations.append({

        "name":station["AddressInfo"]["Title"],

        "latitude":
        station["AddressInfo"]["Latitude"],

        "longitude":
        station["AddressInfo"]["Longitude"],

        "operator":
        station.get("OperatorInfo",{}).get("Title"),

    })


df=pd.DataFrame(stations)

df.to_csv(
    "charging_stations.csv",
    index=False
)


print(df.head())