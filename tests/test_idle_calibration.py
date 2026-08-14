# status-engine/tests/test_idle_calibration.py
"""Calibración del clasificador contra un oráculo INDEPENDIENTE.

`test_idle_golden.py` compara contra etiquetas producidas ejecutando el mismo
notebook del que el motor copió `idle_threshold_low` / `idle_threshold_high`, así
que oráculo e implementación comparten la constante y ese test **no puede
detectar que el corte esté mal ubicado**. Concordaba 97.9–99.7% mientras
producción reportaba 21.9% IDLE contra el 47.6% real del 2026-08-13.

Acá el oráculo son las etiquetas del KMeans k=2 rodante del cliente
(`Disp EnvExp Mold1.json`, 2026-08-07), que decide de forma adaptativa y no
comparte ninguna constante con el motor. Ver `tools/make_kmeans_fixture.py`.

Los dos algoritmos son distintos, así que la concordancia no puede ser exacta:
estos tests son cotas, no igualdad. La cota está puesta donde separa el defecto
del comportamiento correcto — con el umbral mal calibrado la concordancia cae a
~74% y la fracción IDLE a la mitad, ambos muy por fuera.
"""
from pathlib import Path

import pandas as pd
import pytest

from engine.discovery import load_algorithm_specs

FIXTURE = Path(__file__).parent / "fixtures" / "kmeans_03_2026-08-13.csv"
ALGORITHMS_DIR = Path(__file__).resolve().parents[1] / "algorithms"

# Fracción IDLE del oráculo sobre las filas clasificadas: 7971 / 17409.
ORACLE_IDLE_SHARE = 7971 / 17409


@pytest.fixture(scope="module")
def comparison():
    """(pares clasificados, fracción IDLE del motor) usando el algoritmo DESPLEGADO.

    Se carga con el loader de producción y no con `IdleThresholdAlgorithm(...)`
    escrito a mano: el test tiene que romperse si alguien recalibra
    `03_piloto.py`, que es exactamente el archivo que estuvo mal.
    """
    specs = [a for a in load_algorithm_specs(ALGORITHMS_DIR) if a.device_key == "03-piloto"]
    assert len(specs) == 1, f"esperaba un único 03-piloto, encontré {len(specs)}"

    df = pd.read_csv(FIXTURE, parse_dates=["time"])
    actual = list(specs[0].classify(df))
    expected = list(df["expected_state"])

    assert "CERO" not in actual, "el motor no puede emitir muestras sin etiqueta"

    # El notebook deja 'CERO' donde su rama de reclasificación es inalcanzable;
    # el motor siempre etiqueta, así que esas filas no son comparables.
    pairs = [(e, a) for e, a in zip(expected, actual) if e != "CERO"]
    assert pairs, "el fixture no contiene ninguna fila clasificada"
    idle_share = sum(1 for _, a in pairs if a == "IDLE") / len(pairs)
    return pairs, idle_share


def test_deployed_classifier_agrees_with_the_client_algorithm(comparison):
    pairs, _ = comparison
    agreement = sum(1 for e, a in pairs if e == a) / len(pairs)
    assert agreement >= 0.95, (
        f"concordancia {agreement:.1%} contra el KMeans del cliente sobre "
        f"{len(pairs)} muestras del 2026-08-13")


def test_deployed_classifier_reports_the_same_idle_share(comparison):
    """El defecto concreto: la mitad del tiempo ocioso salía como LOAD.

    La concordancia global por sí sola no lo fija — un clasificador podría
    compensar errores en ambas direcciones. Esto ancla la magnitud que consume
    la tarjeta (`idle_minutes`).
    """
    _, idle_share = comparison
    assert abs(idle_share - ORACLE_IDLE_SHARE) <= 0.03, (
        f"el motor reporta {idle_share:.1%} IDLE contra {ORACLE_IDLE_SHARE:.1%} "
        "del algoritmo del cliente")
