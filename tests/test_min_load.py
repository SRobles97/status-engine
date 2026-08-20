import pandas as pd
from engine.smoothing import drop_short_loads


def _t(n, freq="min"):
    return pd.Series(pd.date_range("2026-06-20", periods=n, freq=freq, tz="UTC"))


def test_short_load_run_is_dropped_to_following_state():
    statuses = pd.Series(["OFF", "LOAD", "LOAD", "OFF", "OFF"])
    out = drop_short_loads(statuses, _t(5), min_load_minutes=10)
    assert out.tolist() == ["OFF", "OFF", "OFF", "OFF", "OFF"]


def test_long_load_run_is_preserved():
    statuses = pd.Series(["OFF"] + ["LOAD"] * 15 + ["OFF"])
    out = drop_short_loads(statuses, _t(17), min_load_minutes=10)
    assert out.tolist() == ["OFF"] + ["LOAD"] * 15 + ["OFF"]


def test_run_duration_spans_up_to_the_next_sample():
    # 10 one-minute samples of LOAD occupy 10 minutes, not 9: the run ends when
    # the next sample arrives. Mirrors smooth_statuses' measurement convention.
    statuses = pd.Series(["OFF"] + ["LOAD"] * 10 + ["OFF"])
    out = drop_short_loads(statuses, _t(12), min_load_minutes=10)
    assert out.tolist() == ["OFF"] + ["LOAD"] * 10 + ["OFF"]


def test_short_load_adopts_idle_when_idle_follows():
    # The notebook uses bfill, so the replacement is whatever comes next --
    # not an unconditional OFF.
    statuses = pd.Series(["OFF", "LOAD", "IDLE", "IDLE"])
    out = drop_short_loads(statuses, _t(4), min_load_minutes=10)
    assert out.tolist() == ["OFF", "IDLE", "IDLE", "IDLE"]


def test_trailing_short_load_survives():
    # Nothing follows, so bfill has no source; the notebook's fillna keeps LOAD.
    statuses = pd.Series(["OFF", "OFF", "LOAD"])
    out = drop_short_loads(statuses, _t(3), min_load_minutes=10)
    assert out.tolist() == ["OFF", "OFF", "LOAD"]


def test_leading_short_load_is_dropped():
    statuses = pd.Series(["LOAD", "OFF", "OFF"])
    out = drop_short_loads(statuses, _t(3), min_load_minutes=10)
    assert out.tolist() == ["OFF", "OFF", "OFF"]


def test_zero_threshold_returns_unchanged():
    statuses = pd.Series(["OFF", "LOAD", "OFF"])
    out = drop_short_loads(statuses, _t(3), min_load_minutes=0)
    assert out.tolist() == ["OFF", "LOAD", "OFF"]


def test_empty_input():
    statuses = pd.Series([], dtype=object)
    out = drop_short_loads(statuses, pd.Series([], dtype="datetime64[ns, UTC]"),
                           min_load_minutes=10)
    assert out.tolist() == []


def test_index_is_preserved():
    statuses = pd.Series(["OFF", "LOAD", "OFF"], index=[7, 8, 9])
    out = drop_short_loads(statuses, _t(3), min_load_minutes=10)
    assert out.index.tolist() == [7, 8, 9]


def test_five_second_cadence_three_minute_floor():
    # Revesol's real cadence: 36 samples x 5 s = 3 min. A 35-sample run is
    # shorter than the floor and must go; a 36-sample run must stay.
    short = pd.Series(["OFF"] + ["LOAD"] * 35 + ["OFF"])
    assert drop_short_loads(short, _t(37, "5s"), min_load_minutes=3).tolist() \
        == ["OFF"] * 37
    keep = pd.Series(["OFF"] + ["LOAD"] * 36 + ["OFF"])
    assert drop_short_loads(keep, _t(38, "5s"), min_load_minutes=3).tolist() \
        == ["OFF"] + ["LOAD"] * 36 + ["OFF"]


def test_classify_fills_gaps_before_measuring_load_runs():
    # Orden de las dos pasadas, a través del runner. Dos rachas de LOAD de 2
    # min separadas por un hueco OFF de 1 min. Rellenar primero las une en una
    # sola racha de 5 min que sobrevive al piso de 4 min; medir primero las
    # borraría a ambas por cortas.
    from engine.algorithms import ThresholdAlgorithm
    from engine.runner import _classify

    values = [0.0, 5.0, 5.0, 0.0, 5.0, 5.0, 0.0]
    df = pd.DataFrame({"time": _t(len(values)), "total_current": values})
    algo = ThresholdAlgorithm(
        company="C", device_key="D", power_column="total_current",
        threshold_w=1.0, smoothing_minutes=2, min_load_minutes=4)
    assert _classify(algo, df).tolist() == \
        ["OFF", "LOAD", "LOAD", "LOAD", "LOAD", "LOAD", "OFF"]


def test_classify_without_min_load_leaves_short_runs_alone():
    from engine.algorithms import ThresholdAlgorithm
    from engine.runner import _classify

    values = [0.0, 5.0, 0.0]
    df = pd.DataFrame({"time": _t(len(values)), "total_current": values})
    algo = ThresholdAlgorithm(company="C", device_key="D",
                              power_column="total_current", threshold_w=1.0)
    assert _classify(algo, df).tolist() == ["OFF", "LOAD", "OFF"]
