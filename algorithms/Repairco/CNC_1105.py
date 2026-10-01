from engine.algorithms import ThresholdAlgorithm

# Repairco — CNC 1105 (device_key "1105"), notebook "Producción Disp Repairco 1105".
# Corre sobre el dispositivo ORIGINAL, sin piloto: lee y escribe en el mismo
# device_id con source='algo', en paralelo a los intervalos 'power' del worker
# de umbrales. Lo que muestran tarjetas y reportes lo elige devices.card_source.
#
# Umbral 6 A sobre total_current, relleno de huecos cortos y piso de
# duración para LOAD (las dos pasadas del notebook, en ese orden).
# El notebook cuenta muestras (< 5 y < 3); el motor mide tiempo. Este
# dispositivo reporta cada ~60 s (mediana 2026-09-28..30), así que el corte
# va a mitad de camino entre 4 y 5 muestras (4.5 min) y entre
# 2 y 3 (2.5 min): con un piso exacto de 5 min, el jitter del
# reloj (±0,3 s por muestra) decidiría los huecos de justo 5 muestras.
ALGORITHM = ThresholdAlgorithm(
    company="Repairco",
    device_key="1105",
    power_column="total_current",
    threshold_w=6,
    smoothing_minutes=4.5,
    min_load_minutes=2.5,
)
