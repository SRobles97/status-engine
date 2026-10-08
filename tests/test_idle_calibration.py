# status-engine/tests/test_idle_calibration.py
"""Lo que queda del oráculo independiente del 2026-08-13.

## Qué era esto y por qué se encogió

Mientras el motor corrió la ESCALERA DE UMBRALES, este archivo era la única
prueba de CALIBRACIÓN: comparaba el clasificador desplegado contra las etiquetas
del KMeans del cliente — otro algoritmo, sin ninguna constante en común —, y era
la respuesta al `idle_threshold_low = 18.3` que emitía la mitad del tiempo
ocioso como LOAD (ver docs/2026-08-14-idle-threshold-recalibration.md).

Desde el 2026-09-09 el motor **es** el KMeans del cliente
(docs/2026-09-09-envases-rolling-kmeans.md). Eso disuelve la independencia: la
comparación ya no contrastaría dos algoritmos, sino el mismo consigo mismo. Y
técnicamente tampoco puede correr — el fixture guarda una sola columna
(`total_current`) y `RollingKMeansIdleAlgorithm` consume ocho.

**Cobertura perdida, explícitamente:** ya no hay ninguna prueba de calibración.
Las tres del KMeans (`test_kmeans_golden.py`) son de FIDELIDAD DEL PORT: prueban
que el motor reproduce el notebook, no que el notebook acierte. Que acierte es
ahora responsabilidad del ingeniero del cliente, que es el punto de espejar su
algoritmo en vez de mantener una constante propia — pero conviene saber que
nadie lo verifica de este lado.

**Cómo recuperar el día completo** (vale la pena si alguna vez hay que auditar
la calibración contra un día que no esté en la copia local): exportar el
2026-08-13 del device 66 desde producción con las ocho columnas y volver a
generar el fixture con `tools/make_rolling_kmeans_fixtures.py`. La receta del
export está en el docstring de ese script; sólo hay que cambiar la fecha.

## Qué sigue verificando

El corte contra el APAGADO, que sí depende de una sola columna — y lo verifica
contra las etiquetas del CLIENTE sobre un día que **no** está en la copia local
(la copia llega hasta el 2026-08-10), así que no se solapa con ningún fixture
dorado.
"""
from pathlib import Path

import pandas as pd
import pytest

from engine.discovery import load_algorithm_specs

FIXTURE = Path(__file__).parent / "fixtures" / "kmeans_03_2026-08-13.csv"
ALGORITHMS_DIR = Path(__file__).resolve().parents[1] / "algorithms"


@pytest.fixture(scope="module")
def deployed():
    """El algoritmo DESPLEGADO, cargado con el loader de producción: el test
    tiene que romperse si alguien mueve `off_threshold` en `03.py`."""
    specs = [a for a in load_algorithm_specs(ALGORITHMS_DIR) if a.device_key == "03"]
    assert len(specs) == 1, f"esperaba un único 03, encontré {len(specs)}"
    return specs[0]


def test_the_off_cut_matches_the_client_labels_on_a_day_outside_the_local_copy(deployed):
    """El notebook del 2026-08-07 cortaba en 5 A y el del 2026-09-09 corta en 2 A,
    así que una discrepancia es lo esperado — pero UNA sola.

    Ese es justamente el argumento de que bajar el corte a 2.0 no cambia nada en
    esta máquina: entre 2 y 5 A no hay datos. Si este número crece, la premisa
    dejó de valer y hay que volver a mirar el perfil de corriente antes de
    confiar en `off_threshold`.
    """
    df = pd.read_csv(FIXTURE)
    expected_off = df["expected_state"] == "OFF"
    actual_off = df["total_current"] < deployed.off_threshold
    disagreements = int((expected_off != actual_off).sum())
    assert disagreements <= 1, (
        f"{disagreements} de {len(df)} muestras caen de distinto lado del corte "
        f"OFF ({deployed.off_threshold} A) que en las etiquetas del cliente")
