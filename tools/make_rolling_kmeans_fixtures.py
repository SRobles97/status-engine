"""Genera los CSV dorados del KMeans rodante EJECUTANDO LAS CELDAS DEL NOTEBOOK.

Mismo principio que `make_golden_fixtures.py`: el valor del test dorado depende
de que `expected_state` venga del código del ingeniero del cliente, así que este
script NO reimplementa nada — extrae el source de las celdas y lo corre con
exec() sobre filas reales del device 66.

El notebook es `algoritmos/Envases Exportables/Disp EnvExp Mold1.json` (Colab,
`.json` pese al nombre), en su versión del 2026-09-09, que reemplazó la escalera
de umbrales por un KMeans k=2 reajustado en cada muestra sobre las 120 muestras
previas y 8 columnas.

Celdas de la tubería:
  [10] crea data_with_clusters e inserta las columnas de estado
  [15] CLASIFICACIÓN: OFF por umbral + calentamiento + KMeans rodante
  [17] TRAMOS CORTOS: absorbe huecos de menos de 5 muestras entre dos LOAD

Se omite a propósito:
  [11] llena Sigma/Min5. En ESTA versión del notebook ya no las lee nadie: son
       restos de la escalera de umbrales que sólo sobreviven para el gráfico.
       Correrla no cambiaría una sola etiqueta y cuesta un bucle de 20k
       iteraciones por día.
  [12] es `%%script false` (celda desactivada), [20] y [22] son sólo el gráfico.

## Cómo regenerar los fixtures

Paso 1 — levantar la copia local (`local-dev-db/`, hermano de este repo):

    cd local-dev-db && docker compose up -d && ./restore.sh

Paso 2 — exportar filas crudas, un archivo por día. El `to_char(...)` es
load-bearing: Postgres omite la parte fraccionaria cuando los microsegundos son
exactamente 0, lo que mezcla '...:59+00' con '...:59.663+00' en la misma
columna, y pandas 2.x entonces degrada a dtype `object` EN SILENCIO.

    docker exec -e PGPASSWORD=localdev centinel_local_db psql -U dbmanager -d centineldb -q \
      -c "COPY (SELECT to_char(time AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+00:00' AS time,
                 phase_a_current, phase_b_current, phase_c_current,
                 phase_a_active_power, phase_b_active_power, phase_c_active_power,
                 total_current, total_active_power
          FROM power_measurements
          WHERE device_id = 66 AND (time AT TIME ZONE 'America/Santiago')::date = '2026-08-05'
          ORDER BY time) TO STDOUT WITH CSV HEADER" > raw_03_2026-08-05.csv

Las nueve columnas son obligatorias en ese orden: la celda [10] hace
`insert(9, ...)` (necesita nueve columnas ya presentes) y la celda [15] arma su
matriz con `columnas_kmeans`, de la que `total_current` tiene que quedar en el
índice 6 — el notebook ordena los clústeres con `centroide[6]`.

Paso 3 — correr este script (a diferencia del de la escalera, éste conserva las
ocho columnas en la salida: el clasificador las consume todas):

    python tools/make_rolling_kmeans_fixtures.py raw_03_<dia>.csv \
        tests/fixtures/kmeans_golden_03_<dia>.csv.gz

Días elegidos (mismo criterio que los fixtures de la escalera, para que las dos
familias sean comparables):

| día        | filas | por qué                                                   |
|------------|-------|-----------------------------------------------------------|
| 2026-08-05 | 20742 | día más ocupado, balanceado entre los tres estados        |
| 2026-08-07 | 14251 | parada larga a media jornada — rama OFF y pasada hacia atrás |
| 2026-08-10 |  5959 | día parcial corto — calentamiento y tramos que no juntan ventana |
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

NOTEBOOK = (Path(__file__).resolve().parents[2]
            / "algoritmos" / "Envases Exportables" / "Disp EnvExp Mold1.json")
PIPELINE_CELLS = [10, 15, 17]
# Substring distintivo por índice. `cell_type != "code"` detecta que insertaron
# markdown, pero no que reordenaron celdas de código: el índice seguiría siendo
# "código" y apuntaría al paso equivocado, generando el fixture en silencio con
# la tubería incorrecta.
PIPELINE_CELL_MARKERS = {
    10: "data_with_clusters = df.copy()",
    15: "columnas_kmeans",
    17: "TIEMPOS CORTOS",
}
FEATURE_COLUMNS = [
    "phase_a_current", "phase_b_current", "phase_c_current",
    "phase_a_active_power", "phase_b_active_power", "phase_c_active_power",
    "total_current", "total_active_power",
]


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
                "celdas de código, y este índice ya no apunta al paso que se "
                "espera ahí. Revisá manualmente qué celda corresponde a cada "
                "paso y actualizá PIPELINE_CELLS/PIPELINE_CELL_MARKERS antes de "
                "regenerar fixtures.")
        out.append((i, source))
    return out


def run_notebook_pipeline(df: pd.DataFrame, cells: list[tuple[int, str]]) -> pd.DataFrame:
    # `umbral = 3.12` viene de la celda [6]; la [15] lo reasigna a 19 en su
    # pasada hacia atrás, pero sin un valor inicial una lectura previa sería un
    # NameError — igual que en Colab si se corriera [15] sin haber corrido [6].
    ns = {"np": np, "pd": pd, "KMeans": KMeans, "df": df, "largo": len(df),
          "umbral": 3.12}
    for index, source in cells:
        try:
            exec(compile(source, f"<notebook cell {index}>", "exec"), ns)
        except Exception as exc:
            raise SystemExit(f"la celda {index} falló: {type(exc).__name__}: {exc}")
    return ns["data_with_clusters"]


def main() -> None:
    raw_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])

    df = pd.read_csv(raw_path, parse_dates=["time"])
    if not pd.api.types.is_datetime64_any_dtype(df["time"]):
        raise SystemExit(
            f"{raw_path.name}: la columna 'time' quedó como {df['time'].dtype}, "
            "no datetime — revisá que el export use to_char(..., '...US')")
    expected_columns = ["time"] + FEATURE_COLUMNS
    if list(df.columns) != expected_columns:
        raise SystemExit(
            f"{raw_path.name}: columnas {list(df.columns)}, esperaba "
            f"{expected_columns} en ese orden — la celda [15] ordena los "
            "clústeres por el centroide del índice 6 (total_current)")

    labelled = run_notebook_pipeline(df, notebook_sources(NOTEBOOK, PIPELINE_CELLS))

    golden = labelled[expected_columns + ["Clusters_strg"]].copy()
    golden = golden.rename(columns={"Clusters_strg": "expected_state"})
    golden["time"] = golden["time"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    golden.to_csv(out_path, index=False)

    counts = golden["expected_state"].value_counts().to_dict()
    print(f"{raw_path.name}: {len(golden)} filas -> {out_path.name}")
    print(f"  {counts}")


if __name__ == "__main__":
    main()
