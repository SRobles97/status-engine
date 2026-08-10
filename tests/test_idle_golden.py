# status-engine/tests/test_idle_golden.py
"""Regresión contra las etiquetas reales del notebook.

Las filas 'CERO' son las que el notebook deja sin clasificar (calentamiento y
tramos de borde huérfanos); el motor SÍ las etiqueta, así que la comparación las
excluye y se verifica aparte que el motor nunca emite 'CERO'.
"""
from pathlib import Path

import pandas as pd
import pytest

from engine.algorithms import IdleThresholdAlgorithm

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("golden_03_*.csv"))

ALGORITHM = IdleThresholdAlgorithm(
    company="Envases Exportables",
    device_key="03-piloto",
    source_device_key="03",
    power_column="total_current",
    emits_idle=True,
    smoothing_minutes=0,
)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_engine_reproduces_notebook_labels(path):
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
