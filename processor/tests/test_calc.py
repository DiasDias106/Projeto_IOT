import math

import pytest

from app import calc


def test_haversine_zero_for_same_point():
    assert calc.haversine_km(40.7, -74.0, 40.7, -74.0) == 0.0


def test_haversine_known_distance():
    # Hoboken Terminal -> a point near Grove St PATH, about 2.11 km
    d = calc.haversine_km(40.73594, -74.03030, 40.71959, -74.04312)
    assert math.isclose(d, 2.115, abs_tol=0.005)


def test_haversine_one_degree_latitude():
    assert calc.haversine_km(0, 0, 1, 0) == pytest.approx(111.19, abs=0.01)


def test_energy():
    assert calc.energy_kwh("classic_bike", 3.0) == 0.0
    assert calc.energy_kwh("electric_bike", 2.0) == pytest.approx(0.030)
    assert calc.energy_kwh("electric_bike", None) is None
    assert calc.energy_kwh("classic_bike", None) == 0.0


@pytest.mark.parametrize("member, bike, seconds, expected", [
    ("member", "classic_bike", 45 * 60, 0.00),        # included
    ("member", "classic_bike", 45 * 60 + 1, 0.20),    # 46 billed minutes
    ("member", "classic_bike", 60 * 60, 3.00),        # 15 extra minutes
    ("member", "electric_bike", 10 * 60, 2.50),       # surcharge on every minute
    ("member", "electric_bike", 50 * 60, 13.50),      # 5 * 0.20 + 50 * 0.25
    ("casual", "classic_bike", 1, 4.50),
    ("casual", "classic_bike", 30 * 60, 4.50),
    ("casual", "classic_bike", 31 * 60, 4.80),
    ("casual", "electric_bike", 10 * 60, 8.00),       # 4.50 + 10 * 0.35
    ("casual", "electric_bike", 40 * 60, 21.50),      # 4.50 + 10 * 0.30 + 40 * 0.35
    ("member", "classic_bike", 0, 0.00),
])
def test_cost(member, bike, seconds, expected):
    assert calc.trip_cost_usd(member, bike, seconds) == pytest.approx(expected)


def test_billed_minutes_rounds_up():
    assert calc.billed_minutes(60) == 1
    assert calc.billed_minutes(61) == 2
    assert calc.billed_minutes(0) == 0
