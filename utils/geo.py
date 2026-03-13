"""
Utilitários de geolocalização — centralizados aqui para evitar duplicação.
Anteriormente a função calculate_distance estava copiada em:
  - routers/appointments.py
  - routers/clinics.py
  - routers/emergency.py
  - routers/websocket.py
"""

import math


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calcula a distância em km entre dois pontos geográficos
    usando a fórmula de Haversine.

    Args:
        lat1, lon1: Latitude e longitude do ponto 1
        lat2, lon2: Latitude e longitude do ponto 2

    Returns:
        Distância em quilômetros (float)
    """
    R = 6371  # Raio médio da Terra em km

    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


# Alias para compatibilidade com o código existente
calculate_distance = haversine
