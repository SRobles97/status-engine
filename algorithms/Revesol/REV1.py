from engine.algorithms import ThresholdAlgorithm

# REV1 — corre directo sobre el dispositivo original; reemplaza al piloto
# rev1-piloto (2026-10-08). Algoritmo del notebook "Disp Rev CM1": umbral 1100
# sobre phase_a_active_power, relleno de huecos cortos de 5 min para muestras de 1 min.
ALGORITHM = ThresholdAlgorithm(
    company="Revesol",
    device_key="REV1",
    power_column="phase_a_active_power",
    threshold_w=1100,
    smoothing_minutes=5,
)
