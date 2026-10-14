from engine.algorithms import ThresholdAlgorithm

# Sandvik — Roller (id 83, device_key "Roller"), notebook "Disp Sandvik Roller".
# Corre sobre el dispositivo ORIGINAL, sin piloto: lee y escribe en el mismo
# device_id con source='algo', en paralelo a los intervalos 'power' del worker
# de umbrales. Lo que muestran tarjetas y reportes lo elige devices.card_source.
#
# Umbral 45 A sobre total_current, relleno de huecos cortos y piso de
# duración para LOAD (las dos pasadas del notebook, en ese orden).
# El notebook cuenta muestras (< 60 y < 36); el motor mide tiempo. Este
# dispositivo reporta cada ~5 s (mediana 2026-10-11..14), así que son
# 5 y 3 minutos.
ALGORITHM = ThresholdAlgorithm(
    company="Sandvik",
    device_key="Roller",
    power_column="total_current",
    threshold_w=45,
    smoothing_minutes=5,
    min_load_minutes=3,
)
