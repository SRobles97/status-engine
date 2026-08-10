# status-engine/algorithms/Envases Exportables/03_piloto.py
from engine.algorithms import IdleThresholdAlgorithm

# 03-piloto — clasificado a partir de las mediciones EN VIVO del dispositivo 03
# ("Exportable", id 66), escrito sobre el device_id del piloto. Tres estados:
# OFF / IDLE / LOAD. Los umbrales son de ESTA máquina; la separación IDLE/LOAD
# es menor al 5%, así que ninguna otra puede reutilizarlos sin su propio día de
# calibración. Ver docs/2026-08-10-envases-idle-deploy.md
ALGORITHM = IdleThresholdAlgorithm(
    company="Envases Exportables",
    device_key="03-piloto",
    source_device_key="03",
    power_column="total_current",
    emits_idle=True,
    # El suavizado del motor es LOAD-céntrico: se comería los tramos IDLE cortos,
    # que son justamente el dato pedido. Los tramos cortos se resuelven dentro
    # de classify().
    smoothing_minutes=0,
    off_threshold=5.0,
    idle_threshold_low=18.3,
    idle_threshold_high=19.0,
)
