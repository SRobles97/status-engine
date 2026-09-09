# status-engine/algorithms/Envases Exportables/03_piloto.py
from engine.algorithms import RollingKMeansIdleAlgorithm

# 03-piloto — clasificado a partir de las mediciones EN VIVO del dispositivo 03
# ("Exportable", id 66), escrito sobre el device_id del piloto (74). Tres
# estados: OFF / IDLE / LOAD.
#
# Port del KMeans k=2 rodante de `algoritmos/Envases Exportables/Disp EnvExp
# Mold1.json` (Colab, 2026-09-09), que reemplazó a la escalera de umbrales que
# corrió acá entre el 2026-08-10 y hoy. Ver
# docs/2026-09-09-envases-rolling-kmeans.md.
#
# Lo que cambia respecto de la escalera: ya NO hay una constante calibrada para
# separar IDLE de LOAD. El clasificador reajusta dos clústeres sobre cada
# ventana de 120 muestras (~4 min a 2 s/muestra), así que sigue la meseta ociosa
# en vez de adivinar dónde va a quedar. Eso es lo que hace que este archivo no
# necesite recalibrarse: la meseta de esta máquina se movió entre 17.85 y 18.84 A
# en 16 días y la separación IDLE/LOAD es menor al 5%, que fue exactamente el
# problema de `idle_threshold_low` (18.3 el 2026-08-10, 19.0 el 2026-08-14).
ALGORITHM = RollingKMeansIdleAlgorithm(
    company="Envases Exportables",
    device_key="03-piloto",
    source_device_key="03",
    power_column="total_current",
    emits_idle=True,
    # El suavizado del motor es LOAD-céntrico: se comería los tramos IDLE
    # cortos, que son justamente el dato pedido. Los tramos cortos se resuelven
    # dentro de classify(), con la celda [17] del notebook.
    smoothing_minutes=0,
    # Corte contra el APAGADO, no contra IDLE. El notebook lo bajó de 5.0 a 2.0
    # el 2026-09-09; en esta máquina da igual — la banda 2–5 A tiene entre 0 y 3
    # muestras por día sobre ~15.000 (medido sobre los 6 días de la copia local,
    # y 1 de 17.540 en el fixture del 2026-08-13).
    off_threshold=2.0,
    # Sólo decide los tramos que terminan ANTES de juntar una ventana entera,
    # que el notebook clasifica hacia atrás contra este corte fijo. No participa
    # del régimen normal.
    fallback_threshold=19.0,
    window_samples=120,
)
