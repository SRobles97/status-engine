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
from sklearn.cluster import KMeans


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

    Réplica de la celda [16] del notebook, con DOS rarezas fieles al original
    y UNA desviación deliberada:
    - `cuenta` cuenta SÓLO IDLE y OFF, así que una muestra sin clasificar
      (`UNSET`) es TRANSPARENTE: ni suma ni reinicia el contador. Resolverla
      antes de este paso rompe la clasificación en cada arranque de motor
      (el pico de corriente queda LOAD y reinicia el contador);
    - el bloque que se convierte es el rango POSICIONAL `range(i - cuenta, i)`,
      no la lista de índices del hueco. Con un `UNSET` intercalado los dos
      conjuntos no coinciden;
    - arranca en el índice 0, NO en el índice 1 de la celda [16]. Esta es la
      única desviación deliberada del notebook, y está probada (ver
      test_a_segment_opening_on_a_lone_load_absorbs_a_short_gap): el notebook
      consulta un día completo que SIEMPRE abre con la máquina apagada, así
      que su autor nunca pudo observar un `LOAD` en el índice 0 y `range(1,..)`
      nunca le costó nada. Los tramos de este motor sí pueden abrir en LOAD
      (huecos de reporte >300s cortando el día a mitad de un tramo activo,
      ver `segment_bounds`), y con `range(1,..)` ese LOAD inicial nunca marca
      `started = True`, así que el hueco que lo sigue jamás se absorbe. NO
      restaurar `range(1, len(out))`: los fixtures dorados no lo detectan
      (los tres arrancan en OFF) pero es una regresión real.
    """
    out = list(labels)
    started = False
    cuenta = 0
    for i in range(len(out)):
        if started:
            if out[i] == "IDLE" or out[i] == "OFF":
                cuenta += 1
            if out[i] == "LOAD":
                if cuenta > 0:
                    if cuenta < max_samples:
                        for j in range(i - cuenta, i):
                            out[j] = "LOAD"
                    cuenta = 0
        if not started and out[i] == "LOAD":
            started = True
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


UNSET = "CERO"


def resolve_unlabelled(value: float, params: IdleParams) -> str:
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

    Las muestras que el notebook deja sin clasificar quedan en `UNSET`: esta
    función NO las resuelve. `absorb_short_gaps` necesita verlas intactas para
    que el centinela sea transparente a su contador (ver su docstring); la
    resolución ocurre después, en `IdleThresholdAlgorithm.classify`.
    """
    labels = [UNSET] * len(values)
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

    return labels


def resolve_by_threshold(value: float, off_threshold: float, high_threshold: float) -> str:
    """Decisión de dos umbrales, sin ventanas ni ajuste.

    Es la regla de respaldo del notebook — la que usa en su pasada hacia atrás
    cuando un tramo termina antes de juntar una ventana entera — y también la
    que el motor aplica a las muestras que el notebook deja en `UNSET`.
    """
    if value < off_threshold:
        return "OFF"
    if value > high_threshold:
        return "LOAD"
    return "IDLE"


def classify_segment_rolling_kmeans(
    features: np.ndarray,
    power_index: int,
    *,
    off_threshold: float,
    fallback_threshold: float,
    window: int,
    random_state: int = 42,
) -> List[str]:
    """Máquina de estados del KMeans rodante sobre UN tramo contiguo.

    Port de la celda [15] de `algoritmos/Envases Exportables/Disp EnvExp
    Mold1.json` (Colab, 2026-09-09), que reemplazó la escalera de umbrales.

    `donde`: 0 = apagada, 1 = calentando (el tramo todavía no junta `window`
    muestras), 2 = dentro de un tramo con ventana completa.

    Tres rarezas del notebook que se replican a propósito, porque mueven
    etiquetas reales:

    - la pasada hacia atrás de un tramo corto es `range(i - cuenta, i - 1)`, así
      que **deja la última muestra del tramo sin clasificar**;
    - la PRIMERA muestra de todo tramo largo tampoco se clasifica: la ventana
      retroactiva cubre `[i-119, i]`, que empieza en la segunda;
    - un tramo que termina con `donde == 2` no dispara ninguna pasada hacia
      atrás, porque sus muestras ya se etiquetaron una a una.

    Las dos primeras dejan `UNSET`, que resuelve quien llama (ver
    `absorb_short_gaps`: el centinela tiene que seguir siendo transparente a su
    contador hasta después de la absorción).

    UNA desviación deliberada: una ventana perfectamente plana no tiene dos
    clústeres que separar. KMeans devuelve una partición arbitraria y un
    `ConvergenceWarning` por muestra; acá se cae a `resolve_by_threshold`, que
    es la misma regla de respaldo que el notebook ya usa para los tramos
    cortos. Con datos reales — que siempre traen ruido — esta rama no se toca.
    """
    n = len(features)
    labels: List[str] = [UNSET] * n
    values = features[:, power_index]
    kmeans = KMeans(n_clusters=2, random_state=random_state, n_init="auto")
    pending = 0
    donde = 0

    for i in range(n):
        if values[i] < off_threshold:
            labels[i] = "OFF"
            if donde == 1:
                for j in range(max(0, i - pending), i - 1):
                    labels[j] = ("LOAD" if values[j] > fallback_threshold else "IDLE")
            pending = 0
            donde = 0
        elif donde == 0:
            pending = 1
            donde = 1
        elif donde == 1:
            if pending < window:
                pending += 1
                if pending >= window:
                    donde = 2
        else:
            lo = i - (window - 1)
            chunk = features[lo:i + 1]
            first_pass = pending == window
            if float(np.ptp(chunk, axis=0).max()) == 0.0:
                span = range(lo, i + 1) if first_pass else (i,)
                for j in span:
                    labels[j] = resolve_by_threshold(
                        values[j], off_threshold, fallback_threshold)
            else:
                assigned = kmeans.fit_predict(chunk)
                # El id que devuelve sklearn depende del orden de los datos; la
                # identidad del clúster la fija el centroide de `power_index`.
                if (kmeans.cluster_centers_[0][power_index]
                        > kmeans.cluster_centers_[1][power_index]):
                    assigned = 1 - assigned
                if first_pass:
                    for k in range(window):
                        labels[lo + k] = "LOAD" if assigned[k] == 1 else "IDLE"
                else:
                    labels[i] = "LOAD" if assigned[-1] == 1 else "IDLE"
            if first_pass:
                pending += 1

    return labels
