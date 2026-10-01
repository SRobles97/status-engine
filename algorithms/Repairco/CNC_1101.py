from engine.algorithms import ThresholdAlgorithm

# Repairco — CNC 1101 (device_key "1101"), notebook "Producción Disp Repairco 1101".
# Corre sobre el dispositivo ORIGINAL, sin piloto: lee y escribe en el mismo
# device_id con source='algo', en paralelo a los intervalos 'power' del worker
# de umbrales. Lo que muestran tarjetas y reportes lo elige devices.card_source.
#
# Umbral 13 A sobre total_current, relleno de huecos cortos y piso de
# duración para LOAD (las dos pasadas del notebook, en ese orden).
# El notebook cuenta muestras (150 y 90); el motor mide tiempo. Este
# dispositivo reporta cada ~2 s (mediana 2026-09-28..30), así que son
# 5 y 3 minutos.
ALGORITHM = ThresholdAlgorithm(
    company="Repairco",
    device_key="1101",
    power_column="total_current",
    threshold_w=13,
    smoothing_minutes=5,
    min_load_minutes=3,
)
