from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

ALLOWED_POWER_COLUMNS = frozenset({
    "total_active_power", "total_current", "total_apparent_power",
    "phase_a_active_power", "phase_b_active_power", "phase_c_active_power",
    "phase_a_apparent_power", "phase_b_apparent_power", "phase_c_apparent_power",
    "phase_a_current", "phase_b_current", "phase_c_current",
})


class DegenerateWindowError(Exception):
    """Raised when a window cannot be classified (too few/identical points)."""


@dataclass(frozen=True)
class StatusAlgorithm(ABC):
    company: str
    device_key: str
    power_column: str
    smoothing_minutes: float = 0.0
    # Data-quality guard: a measurement can only be LOAD/IDLE if guard_column >= guard_min.
    # Used to reject implausible spikes (e.g. high power while drawing ~0 current). The
    # guard is applied by the runner before smoothing, so glitches can't anchor smoothing.
    guard_column: Optional[str] = None
    guard_min: float = 0.0
    source_device_key: Optional[str] = None
    # Cuando es True, IDLE sobrevive como estado propio en device_state_intervals
    # en vez de colapsarse a LOAD. Opt-in por algoritmo: los dispositivos ya
    # desplegados (todos ThresholdAlgorithm) conservan el colapso histórico.
    emits_idle: bool = False

    def __post_init__(self):
        if self.power_column not in ALLOWED_POWER_COLUMNS:
            raise ValueError(f"invalid power_column: {self.power_column!r}")
        if self.guard_column is not None and self.guard_column not in ALLOWED_POWER_COLUMNS:
            raise ValueError(f"invalid guard_column: {self.guard_column!r}")

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def classify(self, df: pd.DataFrame) -> pd.Series: ...


@dataclass(frozen=True)
class ThresholdAlgorithm(StatusAlgorithm):
    threshold_w: float = 0.0

    @property
    def name(self) -> str:
        return "threshold"

    def classify(self, df: pd.DataFrame) -> pd.Series:
        values = df[self.power_column].astype(float)
        return pd.Series(
            np.where(values > self.threshold_w, "LOAD", "OFF"),
            index=df.index,
        )


@dataclass(frozen=True)
class KMeansAlgorithm(StatusAlgorithm):
    n_clusters: int = 3

    def __post_init__(self):
        super().__post_init__()
        if self.n_clusters != 3:
            raise ValueError("KMeansAlgorithm supports exactly n_clusters=3")

    @property
    def name(self) -> str:
        return "kmeans"

    def classify(self, df: pd.DataFrame) -> pd.Series:
        values = df[self.power_column].astype(float).to_numpy()
        finite = values[np.isfinite(values)]
        if len(df) < self.n_clusters or len(np.unique(finite)) < self.n_clusters:
            raise DegenerateWindowError(
                f"window has < {self.n_clusters} distinct finite values"
            )
        x = np.nan_to_num(values, nan=0.0).reshape(-1, 1)
        km = KMeans(n_clusters=self.n_clusters, n_init=10, random_state=0).fit(x)
        # rank clusters by ascending center → OFF, IDLE, LOAD
        order = np.argsort(km.cluster_centers_.ravel())
        rank = {cluster: pos for pos, cluster in enumerate(order)}
        ladder = {0: "OFF", 1: "IDLE", 2: "LOAD"}
        return pd.Series(
            [ladder[rank[c]] for c in km.labels_],
            index=df.index,
        )
