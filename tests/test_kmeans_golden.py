# status-engine/tests/test_kmeans_golden.py
"""Fidelidad del PORT contra las etiquetas del notebook del cliente.

`expected_state` NO sale de una reescritura: `tools/make_rolling_kmeans_fixtures.py`
extrae el source de las celdas de `algoritmos/Envases Exportables/Disp EnvExp
Mold1.json` y las ejecuta con exec() sobre filas reales del device 66. El
oráculo es el código del ingeniero del cliente, corrido.

Igual que `test_idle_golden.py`, esto es una prueba de PORT, no de calibración:
demuestra que el motor reproduce el clasificador del notebook, no que el
clasificador esté bien. La diferencia con la escalera de umbrales es que este
algoritmo no tiene ninguna constante que calibrar para la banda IDLE — se
re-centra en cada ventana —, así que la fidelidad del port es casi todo lo que
hay que verificar. Ver docs/2026-09-09-envases-rolling-kmeans.md.

Las constantes van EXPLÍCITAS y no heredadas del algoritmo desplegado: son las
del notebook que generó estos fixtures y tienen que quedar clavadas a ellos.
"""
from pathlib import Path

import pandas as pd
import pytest

from engine.algorithms import RollingKMeansIdleAlgorithm

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("kmeans_golden_03_*.csv.gz"))

# Las del notebook 2026-09-09, no las desplegadas.
NOTEBOOK_OFF_THRESHOLD = 2.0
NOTEBOOK_FALLBACK_THRESHOLD = 19.0
NOTEBOOK_WINDOW = 120

ALGORITHM = RollingKMeansIdleAlgorithm(
    company="Envases Exportables",
    device_key="03-piloto",
    source_device_key="03",
    power_column="total_current",
    emits_idle=True,
    smoothing_minutes=0,
    off_threshold=NOTEBOOK_OFF_THRESHOLD,
    fallback_threshold=NOTEBOOK_FALLBACK_THRESHOLD,
    window_samples=NOTEBOOK_WINDOW,
)


def test_the_golden_fixtures_are_present():
    """Sin esto, borrar los fixtures deja el parametrize vacío y la suite pasa
    en verde sin haber comparado nada."""
    assert len(FIXTURES) >= 3, f"esperaba al menos 3 fixtures, encontré {FIXTURES}"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name.split(".")[0])
def test_engine_reproduces_notebook_labels(path):
    df = pd.read_csv(path, parse_dates=["time"])
    actual = list(ALGORITHM.classify(df))

    assert "CERO" not in actual, "el motor no puede emitir muestras sin etiqueta"

    expected = list(df["expected_state"])
    # El notebook deja 'CERO' donde su pasada de reclasificación no llega
    # (la primera muestra de cada tramo largo, la última de cada tramo corto);
    # el motor SÍ las resuelve, así que no son comparables.
    compared = [(i, e, a) for i, (e, a) in enumerate(zip(expected, actual)) if e != "CERO"]
    assert compared, "el fixture no contiene ninguna fila clasificada"

    mismatches = [(i, e, a) for i, e, a in compared if e != a]
    assert not mismatches, (
        f"{len(mismatches)}/{len(compared)} muestras difieren del notebook; "
        f"primeras: {mismatches[:5]}")


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name.split(".")[0])
def test_the_fixture_day_exercises_all_three_states(path):
    """Un fixture que sólo contiene OFF pasaría el test de arriba sin ejercer
    nada del KMeans."""
    present = set(pd.read_csv(path, usecols=["expected_state"])["expected_state"])
    assert {"OFF", "IDLE", "LOAD"} <= present, f"{path.name} sólo tiene {present}"


ALGORITHMS_DIR = Path(__file__).resolve().parents[1] / "algorithms"


def test_the_deployed_03_piloto_is_this_algorithm_with_these_constants():
    """Ata el archivo DESPLEGADO a las constantes contra las que se compara el
    fixture dorado.

    Sin esto la suite queda verde mientras producción corre otra cosa, que es
    exactamente lo que pasó con `idle_threshold_low = 18.3`: el test dorado
    heredaba las constantes del notebook y nadie comparaba contra el archivo que
    realmente se despliega. Se carga con el loader de producción a propósito.
    """
    from engine.algorithms import KMEANS_FEATURE_COLUMNS
    from engine.discovery import load_algorithm_specs

    specs = [a for a in load_algorithm_specs(ALGORITHMS_DIR) if a.device_key == "03-piloto"]
    assert len(specs) == 1, f"esperaba un único 03-piloto, encontré {len(specs)}"
    algo = specs[0]

    assert isinstance(algo, RollingKMeansIdleAlgorithm)
    assert algo.off_threshold == NOTEBOOK_OFF_THRESHOLD
    assert algo.fallback_threshold == NOTEBOOK_FALLBACK_THRESHOLD
    assert algo.window_samples == NOTEBOOK_WINDOW
    assert algo.emits_idle is True
    assert algo.source_device_key == "03", "el piloto se clasifica con las medidas del 03"
    assert algo.smoothing_minutes == 0, (
        "el suavizado del motor es LOAD-céntrico y se comería los tramos IDLE "
        "cortos, que son justamente el dato pedido")
    assert (set(algo.extra_input_columns) | {algo.power_column}
            == set(KMEANS_FEATURE_COLUMNS))
