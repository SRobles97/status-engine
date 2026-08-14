"""Genera un fixture dorado desde 'Comparación EvnExp.xlsx' — un oráculo INDEPENDIENTE.

## Por qué existe este script además de make_golden_fixtures.py

`make_golden_fixtures.py` produce sus etiquetas ejecutando las celdas de
`Desarrollo/Desarrollo Disp EnvExp Mold1.ipynb`, que es de donde el motor copió
`idle_threshold_low=18.3` / `idle_threshold_high=19.0`. Oráculo e implementación
comparten la constante, así que ese test dorado **no puede detectar que el corte
esté en el lugar equivocado**: los tres fixtures concordaban 97.9–99.7% mientras
producción reportaba 21.9% IDLE contra el 47.6% real (2026-08-13).

Este script usa la otra fuente: el Excel que el ingeniero del cliente exportó
desde `algoritmos/Envases Exportables/Disp EnvExp Mold1.json` (Colab, 2026-08-07,
`.json` pese al nombre). Ese notebook **reemplazó la escalera de umbrales por un
KMeans k=2 rodante sobre 120 muestras** y 8 columnas, así que decide de forma
adaptativa y no comparte ninguna constante con el motor. Sus etiquetas sirven
como oráculo de CALIBRACIÓN; las de make_golden_fixtures.py sólo sirven como
oráculo de FIDELIDAD DEL PORT.

Cómo distinguir de qué notebook salió un export cualquiera: mirar la columna
`umbral`. El KMeans la deja en 0.0 en casi todas las filas (sólo escribe 19.0 en
la pasada hacia atrás de tramos cortos: 89 filas de 17540); la escalera escribe
18.3/19.0 en casi todas.

## Dos trampas del archivo, ambas manejadas acá

1. **Separador decimal.** Excel en locale español leyó el '.' como separador de
   miles en los valores de 3 decimales: `18.389` quedó como el entero `18389`.
   Los valores que no formaban un grupo de 3 dígitos válido (`0.16`, `114.06`)
   sobrevivieron como texto. Regla exacta: celda str -> float() directo;
   celda numérica -> dividir por 1000. Las etiquetas se calcularon en Python
   ANTES de exportar, así que no están afectadas.
2. **La columna `hora` está en UTC**, no en America/Santiago. El día crudo va de
   07:41:10 a 17:26:15 local = 11:41:10 a 21:26:15 UTC.

Uso:

    python tools/make_kmeans_fixture.py \\
        "../algoritmos/Envases Exportables/Comparación EvnExp.xlsx" \\
        tests/fixtures/kmeans_03_2026-08-13.csv

Requiere `openpyxl` (no es dependencia del motor; instalar sólo para regenerar).
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import openpyxl

DAY = dt.date(2026, 8, 13)
LABELS = {"OFF", "IDLE", "LOAD", "CERO"}

# Contrastes contra la BD de producción (consultados 2026-08-14 sobre device_id 66,
# día local 2026-08-13). Si el Excel se regenera para otro día hay que actualizarlos
# o el script aborta — que es el punto: un fixture mal decodificado es peor que
# ninguno, porque el test seguiría verde sobre datos inventados.
EXPECT_ROWS = 17540
EXPECT_ON_SAMPLES = 11203   # count(*) FILTER (WHERE total_current >= 5)
EXPECT_OFF_SAMPLES = 6337   # count(*) FILTER (WHERE total_current <  5)

COL_HORA, COL_CURRENT, COL_LABEL = 1, 8, 10


def decode_current(value) -> float:
    """Deshace el destrozo del locale español. Ver el docstring del módulo."""
    if isinstance(value, str):
        return float(value)
    return float(value) / 1000.0


def main() -> None:
    xl_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    ws = openpyxl.load_workbook(xl_path, read_only=True).worksheets[0]

    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        hora, current, label = row[COL_HORA], row[COL_CURRENT], row[COL_LABEL]
        if hora is None or current is None or label not in LABELS:
            continue
        stamp = dt.datetime.combine(DAY, hora, tzinfo=dt.timezone.utc)
        rows.append((stamp, decode_current(current), label))

    if len(rows) != EXPECT_ROWS:
        raise SystemExit(f"esperaba {EXPECT_ROWS} filas, encontré {len(rows)}")

    on = sum(1 for _, c, _ in rows if c >= 5)
    off = len(rows) - on
    if (on, off) != (EXPECT_ON_SAMPLES, EXPECT_OFF_SAMPLES):
        raise SystemExit(
            f"la decodificación decimal no cuadra con la BD: on={on} off={off}, "
            f"esperaba on={EXPECT_ON_SAMPLES} off={EXPECT_OFF_SAMPLES}")

    out_of_range = [c for _, c, _ in rows if not 0.0 <= c <= 200.0]
    if out_of_range:
        raise SystemExit(f"{len(out_of_range)} valores fuera de rango: {out_of_range[:5]}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write("time,total_current,expected_state\n")
        for stamp, current, label in rows:
            fh.write(f"{stamp.strftime('%Y-%m-%dT%H:%M:%S.%f%z')},{current},{label}\n")

    counts: dict[str, int] = {}
    for _, _, label in rows:
        counts[label] = counts.get(label, 0) + 1
    print(f"{xl_path.name}: {len(rows)} filas -> {out_path.name}")
    print(f"  {counts}")


if __name__ == "__main__":
    main()
