import numpy as np
from datetime import datetime, timedelta, timezone

from engine.idle_rules import (
    IdleParams, select_threshold,
    absorb_short_gaps, segment_bounds, classify_segment,
)

P = IdleParams(off_threshold=5.0, idle_threshold_low=18.3, idle_threshold_high=19.0,
               sigma_window=59, min_window=24)


def test_very_stable_sigma_picks_the_low_threshold():
    # Notebook U3: sigma < 0.5 means we are deep inside a steady stretch
    assert select_threshold(0.4, 18.5, P.idle_threshold_low, P.idle_threshold_high) == 18.3


def test_moderate_sigma_picks_the_high_threshold():
    assert select_threshold(1.2, 17.0, P.idle_threshold_low, P.idle_threshold_high) == 19.0
    assert select_threshold(1.2, 18.5, P.idle_threshold_low, P.idle_threshold_high) == 19.0


def test_short_idle_gap_between_loads_is_absorbed():
    labels = ["LOAD"] * 3 + ["IDLE"] * 3 + ["LOAD"] * 3
    assert absorb_short_gaps(labels, max_samples=5) == ["LOAD"] * 9


def test_long_idle_gap_between_loads_survives():
    labels = ["LOAD"] * 3 + ["IDLE"] * 7 + ["LOAD"] * 3
    assert absorb_short_gaps(labels, max_samples=5) == labels


def test_gap_before_the_first_load_is_never_absorbed():
    # `empieza` in the notebook: absorption only starts after the first LOAD
    labels = ["IDLE", "IDLE", "LOAD", "LOAD"]
    assert absorb_short_gaps(labels, max_samples=5) == labels


def test_trailing_gap_is_not_absorbed():
    labels = ["LOAD", "LOAD", "IDLE", "IDLE"]
    assert absorb_short_gaps(labels, max_samples=5) == labels


def _times(offsets_seconds):
    base = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    return [base + timedelta(seconds=s) for s in offsets_seconds]


def test_contiguous_samples_are_one_segment():
    assert segment_bounds(_times([0, 2, 4, 6]), gap_seconds=300) == [(0, 4)]


def test_a_long_hole_splits_the_window():
    # 2-hour hole between index 1 and 2
    assert segment_bounds(_times([0, 2, 7202, 7204]), gap_seconds=300) == [(0, 2), (2, 4)]


def test_empty_input_has_no_segments():
    assert segment_bounds([], gap_seconds=300) == []


def test_below_off_threshold_is_off():
    values = np.array([1.0, 2.0, 3.0])
    zeros = np.zeros(3)
    assert classify_segment(values, zeros, zeros, P) == ["OFF", "OFF", "OFF"]


def test_unlabelled_sample_does_not_reset_the_gap_counter():
    # El pico de arranque del motor queda sin clasificar en el notebook. Si se
    # resolviera a LOAD antes de absorber, reiniciaría el contador y el IDLE
    # siguiente se absorbería. Con 6 OFF previos el hueco mide 7 >= 5, así que
    # NADA debe absorberse.
    labels = ["LOAD"] + ["OFF"] * 6 + ["CERO", "IDLE", "LOAD"]
    assert absorb_short_gaps(labels, max_samples=5) == labels
