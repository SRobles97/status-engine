from engine.algorithms import ThresholdAlgorithm

# Tubera-1 piloto — classified from the ORIGINAL Tubera-1's live measurements
# (source_device_key "02"), written to Tubera-1 piloto's own device_id. Same algorithm
# as Tubera-1 (from the "Disp Formac Tub1" notebook: umbral 30 on total_current, 5-min
# short-gap fill for 5-second samples).
ALGORITHM = ThresholdAlgorithm(
    company="Formac",
    device_key="tubera-piloto",
    source_device_key="02",
    power_column="total_current",
    threshold_w=30,
    smoothing_minutes=5,
)
