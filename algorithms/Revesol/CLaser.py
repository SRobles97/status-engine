from engine.algorithms import ThresholdAlgorithm

# Cortadora Láser — corre directo sobre el dispositivo original ("c laser");
# reemplaza al piloto claser-piloto (2026-10-08). Mismo algoritmo que el
# notebook "Disp Revesol CLaser": umbral 11 sobre total_current, relleno de
# huecos cortos y piso de duración para LOAD.
#
# El notebook cuenta muestras (60 para el relleno, 36 para el piso de LOAD);
# acá van en minutos porque el motor mide tiempo. A las 5 s por muestra que
# reporta el dispositivo, son 5 y 3 minutos respectivamente.
ALGORITHM = ThresholdAlgorithm(
    company="Revesol",
    device_key="c laser",
    power_column="total_current",
    threshold_w=11,
    smoothing_minutes=5,
    min_load_minutes=3,
)
