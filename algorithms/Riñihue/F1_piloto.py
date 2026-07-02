from engine.algorithms import ThresholdAlgorithm

# F1-piloto — classified from the ORIGINAL F1's live measurements (source_device_key),
# written to F1-piloto's own device_id. Same algorithm as F1 (see FR_S1.py).
ALGORITHM = ThresholdAlgorithm(
    company="Riñihue",
    device_key="F1-piloto",
    source_device_key="F1",
    power_column="phase_a_active_power",
    threshold_w=1100,
    smoothing_minutes=10,
    guard_column="total_current",
    guard_min=1.0,
)
