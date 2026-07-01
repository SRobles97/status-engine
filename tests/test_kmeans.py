import numpy as np
import pandas as pd
import pytest
from engine.algorithms import KMeansAlgorithm, DegenerateWindowError


def _df(values):
    return pd.DataFrame({
        "time": pd.date_range("2026-06-20", periods=len(values), freq="min", tz="UTC"),
        "total_active_power": values,
    })


def test_kmeans_maps_three_levels_by_ascending_mean():
    rng = np.random.default_rng(0)
    low = rng.normal(5, 0.3, 40)        # OFF
    mid = rng.normal(900, 5, 40)        # IDLE
    high = rng.normal(5000, 20, 40)     # LOAD
    df = _df(list(low) + list(mid) + list(high))
    out = KMeansAlgorithm("C", "D", "total_active_power", n_clusters=3).classify(df)
    assert set(out.unique()) == {"OFF", "IDLE", "LOAD"}
    assert out.iloc[:40].mode()[0] == "OFF"
    assert out.iloc[40:80].mode()[0] == "IDLE"
    assert out.iloc[80:].mode()[0] == "LOAD"


def test_kmeans_too_few_distinct_values_raises():
    df = _df([3.0, 3.0, 3.0, 3.0])
    with pytest.raises(DegenerateWindowError):
        KMeansAlgorithm("C", "D", "total_active_power").classify(df)


def test_kmeans_rejects_non_three_clusters():
    with pytest.raises(ValueError):
        KMeansAlgorithm("C", "D", "total_active_power", n_clusters=2)
