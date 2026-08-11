from fastapi import FastAPI
from backend.app.api import routes, stations


app = FastAPI(
    title="RouteVolt API",
    description="AI powered EV route planning system",
    version="1.0"
)


app.include_router(routes.router)
app.include_router(stations.router)


@app.get("/")
def home():
    return {
        "message": "RouteVolt backend running 🚗⚡"
    }