# status-engine/tests/test_idle_golden.py
"""Fidelidad del PORT contra las etiquetas reales del notebook.

Esto verifica UNA sola cosa: que el motor reproduce la máquina de estados de
`Desarrollo/Desarrollo Disp EnvExp Mold1.ipynb` **cuando se le dan las
constantes de ese notebook**. Es una prueba de port, no de calibración.

**NO puede detectar un umbral mal ubicado**, y de hecho no lo detectó: los
fixtures se generan ejecutando las celdas de ese mismo notebook
(`tools/make_golden_fixtures.py`), del que el motor copió 18.3 / 19.0, así que
oráculo e implementación comparten la constante. Concordaban 97.9–99.7%
mientras producción reportaba 21.9% IDLE contra el 47.6% real del 2026-08-13.
La calibración se verifica en `test_idle_calibration.py`, contra un oráculo
independiente.

Por eso las constantes van EXPLÍCITAS acá y no heredadas de los defaults de
`IdleThresholdAlgorithm`: son las del notebook que generó estos fixtures y
tienen que quedar clavadas a ellos. El valor desplegado ya no es 18.3 — vive en
`algorithms/Envases Exportables/03_piloto.py` y se recalibró el 2026-08-14.

Las filas 'CERO' son las que el notebook deja sin clasificar (calentamiento y
tramos de borde huérfanos); el motor SÍ las etiqueta, así que la comparación las
excluye y se verifica aparte que el motor nunca emite 'CERO'.
"""
from pathlib import Path

import pandas as pd
import pytest

from engine.algorithms import IdleThresholdAlgorithm

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("golden_03_*.csv"))

# Las constantes del notebook que generó estos fixtures. NO son las desplegadas.
NOTEBOOK_IDLE_LOW = 18.3
NOTEBOOK_IDLE_HIGH = 19.0

ALGORITHM = IdleThresholdAlgorithm(
    company="Envases Exportables",
    device_key="03-piloto",
    source_device_key="03",
    power_column="total_current",
    emits_idle=True,
    smoothing_minutes=0,
    idle_threshold_low=NOTEBOOK_IDLE_LOW,
    idle_threshold_high=NOTEBOOK_IDLE_HIGH,
)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_engine_reproduces_notebook_labels_given_notebook_constants(path):
    df = pd.read_csv(path, parse_dates=["time"])
    actual = list(ALGORITHM.classify(df))

    assert "CERO" not in actual, "el motor no puede emitir muestras sin etiqueta"

    expected = list(df["expected_state"])
    compared = [(i, e, a) for i, (e, a) in enumerate(zip(expected, actual)) if e != "CERO"]
    assert compared, "el fixture no contiene ninguna fila clasificada"

    mismatches = [(i, e, a) for i, e, a in compared if e != a]
    assert not mismatches, (
        f"{len(mismatches)} de {len(compared)} muestras difieren; "
        f"primeras 5: {mismatches[:5]}"
    )


def test_fixtures_are_present():
    assert FIXTURES, "faltan los CSV dorados en tests/fixtures/"
