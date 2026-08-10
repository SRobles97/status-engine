"""Reglas puras de clasificación de tres estados (OFF/IDLE/LOAD).

Port de `Desarrollo Disp EnvExp Mold1.ipynb` (umbrales diferenciados). Sin I/O y
sin estado: cada función es determinista sobre sus argumentos, para que el test
dorado pueda compararlas contra las etiquetas reales del notebook.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class IdleParams:
    """Parámetros que necesita la máquina de estados.

    `short_gap_samples` NO vive aquí: la absorción de tramos cortos es una
    función aparte que lo recibe directo, así que guardarlo en este objeto
    sería un campo que nadie lee.
    """
    off_threshold: float
    idle_threshold_low: float
    idle_threshold_high: float
    sigma_window: int
    min_window: int


def rolling_sigma(values: np.ndarray, window: int) -> np.ndarray:
    """Desviación estándar poblacional de las `window` muestras ANTERIORES.

    El notebook usa `np.std(values[i-59:i])` — excluye la muestra actual — y deja
    en 0.0 todo lo anterior al primer tramo completo.
    """
    out = np.zeros(len(values), dtype=float)
    for i in range(window, len(values)):
        out[i] = float(np.std(values[i - window:i]))
    return out


def rolling_min(values: np.ndarray, window: int) -> np.ndarray:
    """Mínimo de las `window` muestras ANTERIORES (notebook: `Min5`)."""
    out = np.zeros(len(values), dtype=float)
    for i in range(window, len(values)):
        out[i] = float(np.min(values[i - window:i]))
    return out


def select_threshold(sigma: float, min5: float, low: float, high: float) -> float:
    """Escalera U1–U4 del notebook.

    sigma < 0.5  → tramo muy estable, umbral bajo (U3).
    En cualquier otro caso el notebook asigna el mismo valor alto para todos los
    rangos de Min5 (U1/U2/U4 quedaron numéricamente iguales tras la calibración).
    """
    if sigma < 0.5:
        return low
    return high


def absorb_short_gaps(labels: List[str], max_samples: int) -> List[str]:
    """"Tramos cortos": un hueco de IDLE/OFF entre dos LOAD, más corto que
    `max_samples`, pasa a LOAD.

    Sólo actúa después del primer LOAD (`empieza` en el notebook) y sólo cuando
    el hueco CIERRA contra otro LOAD, así que un hueco final nunca se absorbe.
    """
    out = list(labels)
    started = False
    gap: List[int] = []
    for i, label in enumerate(out):
        if label == "LOAD":
            if started and 0 < len(gap) < max_samples:
                for j in gap:
                    out[j] = "LOAD"
            gap = []
            started = True
        elif started:
            gap.append(i)
    return out


def segment_bounds(times: Sequence[datetime], gap_seconds: float) -> List[Tuple[int, int]]:
    """Divide la ventana en tramos [inicio, fin) sin huecos mayores a `gap_seconds`.

    El notebook consulta un día contiguo, así que su ventana de 59 muestras son
    59 segundos reales. En el motor la ventana puede tener huecos de horas: sin
    esta división, sigma se calcularía ATRAVESANDO el hueco.
    """
    if not times:
        return []
    bounds: List[Tuple[int, int]] = []
    start = 0
    for i in range(1, len(times)):
        if (times[i] - times[i - 1]).total_seconds() > gap_seconds:
            bounds.append((start, i))
            start = i
    bounds.append((start, len(times)))
    return bounds


_UNSET = "CERO"


def _fallback(value: float, params: IdleParams) -> str:
    """Etiqueta de respaldo para muestras que el notebook deja en 'CERO'.

    El notebook admite dejar muestras sin clasificar (calentamiento de las
    ventanas móviles, y tramos de borde que nunca se resuelven porque su rama
    de reclasificación es inalcanzable). El motor escribe una etiqueta por
    muestra, así que estas caen a una decisión de dos umbrales.
    """
    if value < params.off_threshold:
        return "OFF"
    if value > params.idle_threshold_high:
        return "LOAD"
    return "IDLE"


def _label_backwards(labels: List[str], values: np.ndarray,
                     lo: int, hi: int, threshold: float) -> None:
    for j in range(max(0, lo), hi):
        labels[j] = "LOAD" if values[j] > threshold else "IDLE"


def classify_segment(values: np.ndarray, sigma: np.ndarray,
                     min5: np.ndarray, params: IdleParams) -> List[str]:
    """Máquina de estados del notebook sobre UN tramo contiguo.

    `donde`: 0 = apagada, 1 = borde (sigma alta, clasificación diferida),
    2 = dentro de un tramo estable.
    """
    labels = [_UNSET] * len(values)
    threshold = params.idle_threshold_high
    pending = 0
    donde = 0

    for i, value in enumerate(values):
        if value < params.off_threshold:
            labels[i] = "OFF"
            if donde == 1:
                _label_backwards(labels, values, i - pending, i - 1,
                                 params.idle_threshold_high)
            pending = 0
            donde = 0
        elif donde != 2:
            if sigma[i] > 2:
                pending += 1
                donde = 1
            else:
                threshold = select_threshold(
                    sigma[i], min5[i],
                    params.idle_threshold_low, params.idle_threshold_high)
                donde = 2
        else:
            labels[i] = "LOAD" if value > threshold else "IDLE"

    return [lbl if lbl != _UNSET else _fallback(values[i], params)
            for i, lbl in enumerate(labels)]
