from engine.algorithms import ThresholdAlgorithm

# Forestal Riñihue — device_key "F1" (display "FR S1"), from the "Disp FR SH2" notebook.
ALGORITHM = ThresholdAlgorithm(
    company="Riñihue",
    device_key="F1",
    power_column="phase_a_active_power",
    threshold_w=1100,
    smoothing_minutes=10,
    # phase_a_active_power glitches on F1 (e.g. 10 kW spikes at ~0 A). Reject any
    # "load" that draws essentially no current — it's a sensor artifact, not work.
    guard_column="total_current",
    guard_min=1.0,
)
