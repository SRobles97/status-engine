from engine.algorithms import ThresholdAlgorithm

# TBX-P piloto — classified from the ORIGINAL TBX-P's live measurements
# (source_device_key), written to TBX-P piloto's own device_id. Same algorithm as
# TBX-P (from the "Disp TBX_P" notebook: umbral 21 on total_current, 5-min short-gap fill
# for 2-second samples).
ALGORITHM = ThresholdAlgorithm(
    company="Tubexa",
    device_key="tbxp-piloto",
    source_device_key="LS TBX",
    power_column="total_current",
    threshold_w=21,
    smoothing_minutes=5,
)
