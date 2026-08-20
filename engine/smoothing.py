from __future__ import annotations

import pandas as pd


def smooth_statuses(
    statuses: pd.Series, times: pd.Series, smoothing_minutes: float
) -> pd.Series:
    s = list(statuses)
    t = list(times)
    if smoothing_minutes <= 0 or not s:
        return pd.Series(s, index=statuses.index)

    started = False
    gap: list[int] = []  # positional indices of the current non-LOAD run
    for i in range(len(s)):
        if s[i] == "LOAD":
            if started and gap:
                duration_min = (t[i] - t[gap[0]]).total_seconds() / 60.0
                if duration_min < smoothing_minutes:
                    for j in gap:
                        s[j] = "LOAD"
            gap = []
            started = True
        elif started:
            gap.append(i)
    return pd.Series(s, index=statuses.index)


def drop_short_loads(
    statuses: pd.Series, times: pd.Series, min_load_minutes: float
) -> pd.Series:
    """Borra las rachas de LOAD más cortas que min_load_minutes.

    Segunda pasada de los notebooks de Revesol (Torno Hyunday / Cortadora
    Láser), que corre DESPUÉS de smooth_statuses: primero se rellenan los
    huecos cortos, recién entonces se miden las rachas de LOAD.

    La racha se reemplaza por la etiqueta de la muestra siguiente — el `bfill`
    del notebook, no un OFF incondicional: con un algoritmo de tres estados el
    vecino puede ser IDLE. Una racha final sin muestra posterior no tiene de
    dónde copiar y sobrevive como LOAD (el `fillna('LOAD')` del notebook).

    La duración se mide hasta la muestra siguiente, igual que smooth_statuses,
    para que n muestras equivalgan a n intervalos de muestreo.
    """
    s = list(statuses)
    t = list(times)
    if min_load_minutes <= 0 or not s:
        return pd.Series(s, index=statuses.index)

    i = 0
    while i < len(s):
        if s[i] != "LOAD":
            i += 1
            continue
        run_start = i
        while i < len(s) and s[i] == "LOAD":
            i += 1
        # i es la primera muestra no-LOAD, o el final de la ventana.
        if i >= len(s):
            break  # racha final: bfill no tiene fuente, se conserva
        duration_min = (t[i] - t[run_start]).total_seconds() / 60.0
        if duration_min < min_load_minutes:
            for j in range(run_start, i):
                s[j] = s[i]
    return pd.Series(s, index=statuses.index)
