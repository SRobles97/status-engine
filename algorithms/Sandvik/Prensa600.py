from engine.algorithms import ThresholdAlgorithm

# Sandvik — Prensa 600 (id 82, device_key "prensa 600"), notebook
# "Copia de Disp Sandvik Prensa600".
# Corre sobre el dispositivo ORIGINAL, sin piloto: lee y escribe en el mismo
# device_id con source='algo', en paralelo a los intervalos 'power' del worker
# de umbrales. Lo que muestran tarjetas y reportes lo elige devices.card_source.
#
# Umbral 15 A sobre total_current, relleno de huecos cortos y piso de
# duración para LOAD (las dos pasadas del notebook, en ese orden).
# El notebook cuenta muestras (< 36 en ambas; su comentario dice 60, el
# código 36); el motor mide tiempo. Este dispositivo reporta cada ~5 s
# (mediana 2026-10-13..16), así que son 3 minutos en las dos pasadas.
ALGORITHM = ThresholdAlgorithm(
    company="Sandvik",
    device_key="prensa 600",
    power_column="total_current",
    threshold_w=15,
    smoothing_minutes=3,
    min_load_minutes=3,
)
