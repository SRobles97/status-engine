from engine.algorithms import KMeansAlgorithm

# Repairco — device_key "Rep10". Example k-means config (the R1–R5 notebooks were
# legacy Excel-upload analyses with no current device). Adjust params per device.
ALGORITHM = KMeansAlgorithm(
    company="Repairco",
    device_key="Rep10",
    power_column="total_active_power",
    n_clusters=3,
    smoothing_minutes=5,
)
