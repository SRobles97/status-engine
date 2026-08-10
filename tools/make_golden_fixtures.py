"""Genera los CSV dorados ejecutando LAS CELDAS DEL NOTEBOOK, no una reescritura.

El valor del test dorado depende de que `expected_state` venga del código del
ingeniero del cliente. Por eso este script NO reimplementa nada: extrae el
source de las celdas del .ipynb y lo ejecuta con exec() sobre datos reales.

Celdas de la tubería en 'Desarrollo Disp EnvExp Mold1.ipynb':
  [10] crea data_with_clusters e inserta las columnas de estado
  [11] llena Sigma (std de las 59 muestras previas) y Min5 (min de las 24 previas)
  [14] CLASIFICACIÓN: la máquina de estados por umbrales diferenciados
  [16] TRAMOS CORTOS: absorbe huecos cortos entre dos LOAD

Se omiten a propósito:
  [12] es sólo `%%script false --no-raise-error` (marca de celda desactivada)
  [13] [15] son markdown
  [19] [20] son sólo para graficar y no tocan Clusters_strg

## Cómo regenerar los fixtures (receta completa)

Paso 1 — levantar una DB local. `local-dev-db/` (hermano de este repo, scp'do
desde el VPS) tiene `docker-compose.yml` + `restore.sh` + un slice de 7 días:

    cd local-dev-db && docker compose up -d && ./restore.sh

Paso 2 — exportar filas crudas, un archivo por día. El `to_char(...)` es
load-bearing: Postgres omite la parte fraccionaria cuando los microsegundos son
exactamente 0, lo que mezcla `...:59+00` con `...:59.663+00` en la misma
columna, y pandas 2.x entonces degrada silenciosamente a dtype `object`:

    docker exec -e PGPASSWORD=localdev centinel_local_db psql -U dbmanager -d centineldb -q \\
      -c "COPY (SELECT to_char(time AT TIME ZONE 'UTC', 'YYYY-MM-DD\\"T\\"HH24:MI:SS.US') || '+00:00' AS time,
                 phase_a_current, phase_b_current, phase_c_current,
                 phase_a_active_power, phase_b_active_power, phase_c_active_power,
                 total_current, total_active_power
          FROM power_measurements
          WHERE device_id = 66 AND (time AT TIME ZONE 'America/Santiago')::date = '2026-08-05'
          ORDER BY time) TO STDOUT WITH CSV HEADER" > raw_03_2026-08-05.csv

Las nueve columnas son necesarias aunque sólo `total_current` alimente la
clasificación: la celda [10] del notebook hace `insert(9, ...)`, que necesita
que el frame ya tenga nueve columnas.

Los límites del día son **hora local de Santiago**, para calzar con la ventana
de medianoche local del motor; los timestamps se renderizan en **UTC** para que
el fixture no pueda derivar con el timezone de la sesión.

Paso 3 — correr este script:

    python tools/make_golden_fixtures.py raw_03_<dia>.csv tests/fixtures/golden_03_<dia>.csv

Por qué se eligieron estos tres días (perfil observado del device 66):

| día        | filas | OFF | banda idle 5–19 A | sobre 19 A | por qué                                    |
|------------|-------|-----|--------------------|------------|---------------------------------------------|
| 2026-08-05 | 20742 | 45% | 35%                | 19%        | día más ocupado, balanceado entre 3 estados |
| 2026-08-07 | 14251 | 65% | 23%                | 11%        | parada larga a media jornada — rama OFF y pasada hacia atrás |
| 2026-08-10 |  5959 | 26% | 53%                | 21%        | día parcial corto — calentamiento y casos degenerados |

También vale la pena registrar que las fechas propias del notebook
(2026-07-30, 2026-08-03) NO fueron utilizables. 07-30 cae fuera del slice de 7
días por completo, y 08-03 está parcial en él (12,556 filas contra las 18,199
del notebook, porque los límites `datetime` naive del notebook resolvían en
otro timezone).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

NOTEBOOK = (Path(__file__).resolve().parents[2]
            / "algoritmos" / "Envases Exportables" / "Desarrollo"
            / "Desarrollo Disp EnvExp Mold1.ipynb")
PIPELINE_CELLS = [10, 11, 14, 16]
# Substring distintivo por índice de celda. El chequeo de cell_type != "code"
# detecta que alguien insertó markdown, pero no que reordenaron o insertaron
# celdas de código: el índice seguiría siendo "código" pero apuntaría a la
# celda equivocada, y el fixture dorado se generaría en silencio con la
# tubería incorrecta. Verificado contra el notebook real (celdas 10/11/14/16).
PIPELINE_CELL_MARKERS = {
    10: "Clusters_strg",  # crea data_with_clusters e inserta las columnas de estado
    11: "Sigma",  # llena Sigma (std de las 59 previas) y Min5 (min de las 24 previas)
    14: "cuenta",  # clasificación por umbrales diferenciados
    16: "empieza",  # tramos cortos: absorbe huecos entre dos LOAD
}


def notebook_sources(path: Path, indices: list[int]) -> list[tuple[int, str]]:
    if not path.exists():
        raise SystemExit(
            f"no se encontró el notebook en {path}\n"
            "Se esperaba en 'algoritmos/' junto a este repo (smartlook/algoritmos/...), "
            "que no forma parte de status-engine.")
    nb = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for i in indices:
        cell = nb["cells"][i]
        if cell["cell_type"] != "code":
            raise SystemExit(f"la celda {i} no es código, es {cell['cell_type']}")
        source = "".join(cell["source"])
        marker = PIPELINE_CELL_MARKERS.get(i)
        if marker is not None and marker not in source:
            raise SystemExit(
                f"la celda {i} no contiene el marcador esperado {marker!r}: el "
                "notebook parece haberse reordenado o le insertaron/borraron "
                "celdas de código, y este índice ya no apunta al paso de la "
                "tubería que se espera ahí. Revisá manualmente cuál celda "
                "corresponde a cada paso y actualizá PIPELINE_CELLS y "
                "PIPELINE_CELL_MARKERS en este script antes de regenerar fixtures.")
        out.append((i, source))
    return out


def run_notebook_pipeline(df: pd.DataFrame, cells: list[tuple[int, str]]) -> pd.DataFrame:
    # `umbral = 90` viene de la celda [6] del notebook. La celda [14] puede leer
    # `umbral` antes de asignarlo (cuando el primer tramo entra con sigma alta),
    # así que sin este valor inicial el exec revienta con NameError — igual que
    # pasaría en Colab si se corriera [14] sin haber corrido [6].
    ns = {"np": np, "pd": pd, "df": df, "largo": len(df), "umbral": 90}
    for index, source in cells:
        try:
            exec(compile(source, f"<notebook cell {index}>", "exec"), ns)
        except Exception as exc:
            raise SystemExit(f"la celda {index} falló: {type(exc).__name__}: {exc}")
    return ns["data_with_clusters"]


def main() -> None:
    raw_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    df = pd.read_csv(raw_path, parse_dates=["time"])
    # Postgres omite la parte fraccionaria cuando los microsegundos son 0, así que
    # un COPY sin to_char() mezcla '...:59+00' con '...:59.663+00'. pandas 2.x ve
    # formatos mixtos y devuelve dtype object EN SILENCIO: el .dt de más abajo
    # revienta, pero peor todavía, el test dorado leería la misma mezcla. El
    # export ya fuerza microsegundos siempre; esto es el cinturón de seguridad.
    if not pd.api.types.is_datetime64_any_dtype(df["time"]):
        raise SystemExit(
            f"{raw_path.name}: la columna 'time' quedó como {df['time'].dtype}, "
            "no datetime — revisá que el export use to_char(..., '...US')")
    cells = notebook_sources(NOTEBOOK, PIPELINE_CELLS)
    labelled = run_notebook_pipeline(df, cells)

    golden = labelled[["time", "total_current", "Clusters_strg"]].copy()
    golden = golden.rename(columns={"Clusters_strg": "expected_state"})
    golden["time"] = golden["time"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    golden.to_csv(out_path, index=False)

    counts = golden["expected_state"].value_counts().to_dict()
    print(f"{raw_path.name}: {len(golden)} filas -> {out_path.name}")
    print(f"  {counts}")


if __name__ == "__main__":
    main()
