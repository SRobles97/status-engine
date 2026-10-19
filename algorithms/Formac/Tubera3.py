from engine.algorithms import ThresholdAlgorithm

# Tubera-3 — corre directo sobre el dispositivo original (device_key "02");
# reemplaza al piloto tubera-piloto. Algoritmo del notebook "Disp Formac Tub1":
# umbral 30 sobre total_current, relleno de huecos cortos de 5 min para muestras de 5 s.
ALGORITHM = ThresholdAlgorithm(
    company="Formac",
    device_key="02",
    power_column="total_current",
    threshold_w=30,
    smoothing_minutes=5,
)
