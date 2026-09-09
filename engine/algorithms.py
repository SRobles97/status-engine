from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

from engine.idle_rules import (
    UNSET,
    IdleParams,
    absorb_short_gaps,
    classify_segment,
    classify_segment_rolling_kmeans,
    resolve_by_threshold,
    resolve_unlabelled,
    rolling_min,
    rolling_sigma,
    segment_bounds,
)

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
    # Piso de duración para LOAD: una racha más corta se descarta y adopta el
    # estado siguiente. Segunda pasada de los notebooks de Revesol, aplicada
    # DESPUÉS de smoothing_minutes. 0.0 la desactiva (comportamiento histórico).
    min_load_minutes: float = 0.0
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
    def extra_input_columns(self) -> tuple:
        """Columnas que `classify` necesita además de `power_column`.

        El runner las suma a las que le pide a la BD. Vacío por defecto: un
        clasificador de una sola columna no necesita nada más.
        """
        return ()

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


@dataclass(frozen=True)
class IdleThresholdAlgorithm(StatusAlgorithm):
    """Clasificador de tres estados por umbrales diferenciados.

    Cada constante del notebook es un campo, así que una segunda máquina es un
    archivo nuevo y no código nuevo. Ver
    docs/2026-08-10-envases-idle-deploy.md.
    """
    off_threshold: float = 5.0
    idle_threshold_low: float = 18.3
    idle_threshold_high: float = 19.0
    sigma_window: int = 59
    # Hoy es inerte: select_threshold ignora min5 (U1/U2/U4 del notebook quedaron
    # numéricamente iguales tras la calibración), así que este valor no cambia
    # ninguna clasificación. Se conserva por fidelidad al notebook — que sigue
    # calculando rolling_min sobre cada muestra — y para una futura recalibración
    # donde U1/U2/U4 vuelvan a divergir.
    min_window: int = 24
    short_gap_samples: int = 5
    gap_seconds: float = 300.0

    @property
    def name(self) -> str:
        return "idle_threshold"

    @property
    def params(self) -> IdleParams:
        return IdleParams(
            off_threshold=self.off_threshold,
            idle_threshold_low=self.idle_threshold_low,
            idle_threshold_high=self.idle_threshold_high,
            sigma_window=self.sigma_window,
            min_window=self.min_window,
        )

    def classify(self, df: pd.DataFrame) -> pd.Series:
        values = df[self.power_column].astype(float).to_numpy()
        times = list(df["time"])
        params = self.params
        labels: list[str] = []
        for lo, hi in segment_bounds(times, self.gap_seconds):
            chunk = values[lo:hi]
            segment = classify_segment(
                chunk,
                rolling_sigma(chunk, self.sigma_window),
                rolling_min(chunk, self.min_window),
                params,
            )
            segment = absorb_short_gaps(segment, self.short_gap_samples)
            # El centinela se resuelve AL FINAL: ver el docstring de
            # absorb_short_gaps. El orden es parte del contrato, no un detalle.
            labels.extend(
                lbl if lbl != UNSET else resolve_unlabelled(chunk[i], params)
                for i, lbl in enumerate(segment)
            )
        return pd.Series(labels, index=df.index)


# Las ocho columnas que el notebook le pasa a KMeans, EN SU ORDEN — el orden es
# parte del contrato: `power_column` se localiza por índice para ordenar los
# clústeres, igual que el `centroide[6]` del notebook.
KMEANS_FEATURE_COLUMNS = (
    "phase_a_current", "phase_b_current", "phase_c_current",
    "phase_a_active_power", "phase_b_active_power", "phase_c_active_power",
    "total_current", "total_active_power",
)


@dataclass(frozen=True)
class RollingKMeansIdleAlgorithm(StatusAlgorithm):
    """Tres estados (OFF/IDLE/LOAD) con un KMeans k=2 REAJUSTADO EN CADA MUESTRA
    sobre las `window_samples` anteriores y `feature_columns` columnas.

    Reemplaza a `IdleThresholdAlgorithm` en `03-piloto`. La diferencia que
    importa es que no lleva ninguna constante calibrada para la banda IDLE: se
    re-centra en cada ventana, así que sigue la meseta ociosa en vez de adivinar
    dónde va a quedar. Ver docs/2026-09-09-envases-rolling-kmeans.md.

    `off_threshold` y `fallback_threshold` sí son constantes, pero ninguna de las
    dos separa IDLE de LOAD en régimen: la primera corta contra el apagado (la
    banda 2-5 A de esta máquina tiene 0-3 muestras por día) y la segunda sólo
    decide tramos que terminan antes de juntar una ventana entera.
    """
    off_threshold: float = 2.0
    fallback_threshold: float = 19.0
    window_samples: int = 120
    short_gap_samples: int = 5
    gap_seconds: float = 300.0
    feature_columns: tuple = KMEANS_FEATURE_COLUMNS
    random_state: int = 42

    def __post_init__(self):
        super().__post_init__()
        if not self.feature_columns:
            raise ValueError("feature_columns cannot be empty")
        for col in self.feature_columns:
            if col not in ALLOWED_POWER_COLUMNS:
                raise ValueError(f"invalid feature column: {col!r}")
        if self.power_column not in self.feature_columns:
            raise ValueError(
                f"power_column {self.power_column!r} must be one of feature_columns "
                "— cluster identity is decided by its centroid")
        if self.window_samples < 2:
            raise ValueError("window_samples must be at least 2")

    @property
    def name(self) -> str:
        return "rolling_kmeans_idle"

    @property
    def extra_input_columns(self) -> tuple:
        """Columnas que el runner tiene que pedirle a la BD además de
        `power_column`. Sin esto el frame llega con dos columnas y KMeans
        clasificaría sobre una sola dimensión, en silencio."""
        return tuple(c for c in self.feature_columns if c != self.power_column)

    def classify(self, df: pd.DataFrame) -> pd.Series:
        missing = [c for c in self.feature_columns if c not in df.columns]
        if missing:
            raise ValueError(f"missing feature columns in window: {missing}")
        features = df[list(self.feature_columns)].astype(float).to_numpy()
        power_index = self.feature_columns.index(self.power_column)
        times = list(df["time"])
        labels: list[str] = []
        for lo, hi in segment_bounds(times, self.gap_seconds):
            chunk = features[lo:hi]
            segment = classify_segment_rolling_kmeans(
                chunk, power_index,
                off_threshold=self.off_threshold,
                fallback_threshold=self.fallback_threshold,
                window=self.window_samples,
                random_state=self.random_state)
            segment = absorb_short_gaps(segment, self.short_gap_samples)
            # El centinela se resuelve AL FINAL, igual que en
            # IdleThresholdAlgorithm: absorb_short_gaps necesita verlo intacto.
            labels.extend(
                lbl if lbl != UNSET else resolve_by_threshold(
                    chunk[i][power_index], self.off_threshold, self.fallback_threshold)
                for i, lbl in enumerate(segment))
        return pd.Series(labels, index=df.index)
