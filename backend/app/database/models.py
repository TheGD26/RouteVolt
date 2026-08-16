"""
ORM models for RouteVolt's SQLite store.

Station mirrors tamil_nadu_ev_charging_stations_CONSOLIDATED.csv column for
column (see backend/app/scripts/seed_stations.py) so charger_service.py can
swap from reading the CSV directly to querying this table without changing
the shape of what it hands back to route_optimizer.py.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.database.db import Base


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_name: Mapped[str] = mapped_column(String, nullable=False)
    operator: Mapped[str] = mapped_column(String, nullable=True)
    address: Mapped[str] = mapped_column(String, nullable=True)
    city: Mapped[str] = mapped_column(String, nullable=True)
    district_area: Mapped[str] = mapped_column(String, nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=True)
    pincode: Mapped[float] = mapped_column(Float, nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    num_chargers: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    connector_types: Mapped[str] = mapped_column(String, nullable=True)
    # Kept as text, not float: the source CSV mixes plain numbers ("30.0")
    # with ranges/units ("3.3-7 kW", "7-120 kW") -- charger_service/
    # route_optimizer already parse this defensively via _clean_number.
    power_kw: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="Operational")
    pricing: Mapped[str] = mapped_column(String, nullable=True)
    source_url: Mapped[str] = mapped_column(String, nullable=True)
    coord_precision: Mapped[str] = mapped_column(String, nullable=True)
    corridor: Mapped[str] = mapped_column(String, nullable=True)


class OccupancyStatus(str, enum.Enum):
    busy = "busy"
    free = "free"
    unknown = "unknown"


class OccupancyReport(Base):
    __tablename__ = "occupancy_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), nullable=False, index=True)
    reported_status: Mapped[OccupancyStatus] = mapped_column(Enum(OccupancyStatus), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String, nullable=False, default="user_report")
