from engine.algorithms import ThresholdAlgorithm

# Repairco — CNC 1107 (device_key "Rep10"), notebook "Producción Disp Repairco 1107 exRep10".
# Corre sobre el dispositivo ORIGINAL, sin piloto: lee y escribe en el mismo
# device_id con source='algo', en paralelo a los intervalos 'power' del worker
# de umbrales. Lo que muestran tarjetas y reportes lo elige devices.card_source.
#
# Umbral 3.3 A sobre total_current, relleno de huecos cortos y piso de
# duración para LOAD (las dos pasadas del notebook, en ese orden).
# El notebook cuenta muestras (< 8 y < 3); el motor mide tiempo. Este
# dispositivo reporta cada ~60 s (mediana 2026-09-28..30), así que el corte
# va a mitad de camino entre 7 y 8 muestras (7.5 min) y entre
# 2 y 3 (2.5 min): con un piso exacto de 8 min, el jitter del
# reloj (±0,3 s por muestra) decidiría los huecos de justo 8 muestras.
ALGORITHM = ThresholdAlgorithm(
    company="Repairco",
    device_key="Rep10",
    power_column="total_current",
    threshold_w=3.3,
    smoothing_minutes=7.5,
    min_load_minutes=2.5,
)
