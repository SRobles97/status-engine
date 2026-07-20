from engine.algorithms import ThresholdAlgorithm

# TBX-O piloto — classified from the ORIGINAL TBX-O's live measurements
# (source_device_key), written to TBX-O piloto's own device_id. Same algorithm as
# TBX-O (from the "Disp TBX_O" notebook: umbral 21 on total_current, 5-min short-gap fill
# for 2-second samples).
ALGORITHM = ThresholdAlgorithm(
    company="Tubexa",
    device_key="tbxo-piloto",
    source_device_key="INF TBX",
    power_column="total_current",
    threshold_w=21,
    smoothing_minutes=5,
)
