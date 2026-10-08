from engine.algorithms import ThresholdAlgorithm

# Torno Hyunday — corre directo sobre el dispositivo original; reemplaza al
# piloto thyunday-piloto (2026-10-08). Mismo algoritmo que el notebook "Disp
# Revesol THyunday": umbral 21.5 sobre total_current, relleno de huecos cortos y piso de duración para LOAD.
#
# El notebook cuenta muestras (36 y 36); acá van en minutos porque el motor mide
# tiempo. A las 5 s por muestra que reporta hoy el dispositivo, 36 muestras son
# 3 minutos. OJO: el notebook fija la ventana en 2026-08-10, cuando este
# dispositivo todavía reportaba cada ~29 s — con esa cadencia los mismos 36
# equivaldrían a 18 min. Se eligió la cadencia actual (5 s).
#
# device_key es "Revesol" porque ese es literalmente el device_key del
# Torno Hyunday (id 72) en la tabla devices, pese a llamarse como la empresa.
ALGORITHM = ThresholdAlgorithm(
    company="Revesol",
    device_key="Revesol",
    power_column="total_current",
    threshold_w=21.5,
    smoothing_minutes=3,
    min_load_minutes=3,
)
