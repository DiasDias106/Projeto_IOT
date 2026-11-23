"""Trip computations: distance, energy and cost (project spec, section 2.1).

Used by the T2 Workers. Pure functions.
"""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0

# Energy: 15 Wh per km of straight-line distance, electric bikes only
EBIKE_WH_PER_KM = 15.0

# Reference tariff
MEMBER_INCLUDED_MIN = 45
MEMBER_EXTRA_PER_MIN_USD = 0.20
CASUAL_UNLOCK_USD = 4.50          # includes 30 min
CASUAL_INCLUDED_MIN = 30
CASUAL_EXTRA_PER_MIN_USD = 0.30
EBIKE_SURCHARGE_PER_MIN_USD = {"member": 0.25, "casual": 0.35}


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance (km) between two points. Same point gives 0."""
    if lat1 == lat2 and lng1 == lng2:
        return 0.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def energy_kwh(rideable_type: str, distance_km: float | None) -> float | None:
    """kWh used: 15 Wh/km for electric_bike, 0 for classic_bike. None if the distance is unknown."""
    if rideable_type != "electric_bike":
        return 0.0
    if distance_km is None:
        return None
    return EBIKE_WH_PER_KM * distance_km / 1000.0


def billed_minutes(duration_s: int) -> int:
    """Started minutes are billed: 61 s is 2 minutes."""
    return max(0, math.ceil(duration_s / 60))


def trip_cost_usd(member_casual: str, rideable_type: str, duration_s: int) -> float:
    """Estimated cost. Minutes are rounded up. The e-bike surcharge applies to every minute."""
    minutes = billed_minutes(duration_s)
    if member_casual == "member":
        cost = MEMBER_EXTRA_PER_MIN_USD * max(0, minutes - MEMBER_INCLUDED_MIN)
    else:
        cost = CASUAL_UNLOCK_USD + CASUAL_EXTRA_PER_MIN_USD * max(0, minutes - CASUAL_INCLUDED_MIN)
    if rideable_type == "electric_bike":
        cost += EBIKE_SURCHARGE_PER_MIN_USD[member_casual] * minutes
    return round(cost, 2)
