"""Cálculos de uma viagem: distância, energia e custo (enunciado, secção 2.1).

Usado pelo T2 Worker. Funções puras, fáceis de testar.
"""
from __future__ import annotations

EARTH_RADIUS_KM = 6371.0

# Energia: 15 Wh por km em linha reta, só para bicicletas elétricas
EBIKE_WH_PER_KM = 15.0

# Tarifário de referência
MEMBER_UNLOCK_USD = 0.0
MEMBER_INCLUDED_MIN = 45          # viagens clássicas até 45 min incluídas
MEMBER_EXTRA_PER_MIN_USD = 0.20
CASUAL_UNLOCK_USD = 4.50          # inclui 30 min
CASUAL_INCLUDED_MIN = 30
CASUAL_EXTRA_PER_MIN_USD = 0.30
EBIKE_SURCHARGE_PER_MIN_USD = {"member": 0.25, "casual": 0.35}


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distância em linha reta (km) entre dois pontos. Viagens circulares dão 0."""
    # TODO(processor/T2): implementar a fórmula de Haversine
    raise NotImplementedError


def energy_kwh(rideable_type: str, distance_km: float) -> float:
    """kWh gastos: EBIKE_WH_PER_KM * distância / 1000 para electric_bike, 0 para classic_bike."""
    # TODO(processor/T2)
    raise NotImplementedError


def trip_cost_usd(member_casual: str, rideable_type: str, duration_s: int) -> float:
    """Custo estimado da viagem segundo o tarifário acima.

    Decidir e documentar no relatório: arredondamento dos minutos (para cima?) e se a
    sobretaxa elétrica se aplica a todos os minutos.
    """
    # TODO(processor/T2)
    raise NotImplementedError
