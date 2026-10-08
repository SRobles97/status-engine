from engine.algorithms import ThresholdAlgorithm

# TBX-P (LS TBX) — corre directo sobre el dispositivo original; reemplaza al
# piloto tbxp-piloto (2026-10-08). Algoritmo del notebook "Disp TBX_P": umbral 21
# sobre total_current, relleno de huecos cortos de 5 min para muestras de 2 s.
ALGORITHM = ThresholdAlgorithm(
    company="Tubexa",
    device_key="LS TBX",
    power_column="total_current",
    threshold_w=21,
    smoothing_minutes=5,
)
