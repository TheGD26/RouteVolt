from fastapi import APIRouter


router = APIRouter(
    prefix="/stations",
    tags=["Charging Stations"]
)


stations = [
    {
        "id":1,
        "name":"Chennai EV Hub",
        "charger":"DC Fast",
        "availability":5
    }
]


@router.get("/")
def get_stations():

    return stations
