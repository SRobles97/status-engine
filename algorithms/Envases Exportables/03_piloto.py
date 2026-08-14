# status-engine/algorithms/Envases Exportables/03_piloto.py
from engine.algorithms import IdleThresholdAlgorithm

# 03-piloto — clasificado a partir de las mediciones EN VIVO del dispositivo 03
# ("Exportable", id 66), escrito sobre el device_id del piloto. Tres estados:
# OFF / IDLE / LOAD. Los umbrales son de ESTA máquina; la separación IDLE/LOAD
# es menor al 5%, así que ninguna otra puede reutilizarlos sin su propio día de
# calibración. Ver docs/2026-08-10-envases-idle-deploy.md
ALGORITHM = IdleThresholdAlgorithm(
    company="Envases Exportables",
    device_key="03-piloto",
    source_device_key="03",
    power_column="total_current",
    emits_idle=True,
    # El suavizado del motor es LOAD-céntrico: se comería los tramos IDLE cortos,
    # que son justamente el dato pedido. Los tramos cortos se resuelven dentro
    # de classify().
    smoothing_minutes=0,
    off_threshold=5.0,
    # Escalón bajo RETIRADO (era 18.3, recalibrado 2026-08-14). `select_threshold`
    # elige el bajo cuando sigma < 0.5 — ~41% de las muestras, o sea justo la
    # meseta ociosa estable — y 18.3 caía DENTRO de la banda IDLE (p5 18.087 /
    # mediana 18.349 / p95 18.739 el 2026-08-13), partiéndola al medio: la mitad
    # del tiempo ocioso se emitía como LOAD. No fue una deriva reciente, la
    # mediana de la meseta estuvo sobre 18.3 en 14 de los 16 días con datos
    # (2026-07-20…08-14, rango 17.85–18.84 A); uno de los dos días por debajo es
    # justamente un día de fixture, por eso pasó la validación original.
    # Igualar ambos escalones deja el corte por encima de toda meseta observada.
    # OJO: son ~0.34 A de margen contra el peor día. Si la meseta sigue
    # derivando esto vuelve, y la salida es portar el KMeans rodante del
    # notebook del cliente (2026-08-07), no mover la constante otra vez.
    idle_threshold_low=19.0,
    idle_threshold_high=19.0,
)
