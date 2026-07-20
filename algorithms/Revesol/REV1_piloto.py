from engine.algorithms import ThresholdAlgorithm

# REV1 piloto — classified from the ORIGINAL REV1's live measurements
# (source_device_key), written to REV1 piloto's own device_id. Same algorithm as
# REV1 (from the "Disp Rev CM1" notebook: umbral 1100 on phase_a_active_power,
# 5-min short-gap fill for 1-minute samples).
ALGORITHM = ThresholdAlgorithm(
    company="Revesol",
    device_key="rev1-piloto",
    source_device_key="REV1",
    power_column="phase_a_active_power",
    threshold_w=1100,
    smoothing_minutes=5,
)
