"""Composition root do Station Manager (controle de rotor): único módulo que
conhece implementações concretas.

Escolhe o rotor (mock ou ZMQ/Rot2Prog) pela flag --rotor, monta o
TrackingService e o SatelliteTrackingService, e expõe via ZMQ REP dois modos
de operação:

- apontamento manual, consumido pelo GRS Manager (que traduz do gpredict);
- rastreamento autônomo, consumido pelo TC Scheduler, em que a estação conduz
  a passagem inteira sozinha a partir dos dados orbitais.

Nem o núcleo, nem os adapters, conhecem uns aos outros — só este módulo os amarra.
"""

from __future__ import annotations

import argparse
import os
import logging

from spacelab_tracking import config as tracking_config

from mgm8.application.satellite_tracking_service import (
    DEFAULT_UPDATE_INTERVAL_SECONDS,
    SatelliteTrackingService,
)
from mgm8.application.tracking_service import TrackingService
from mgm8.domain.models import parse_radios
from mgm8.domain.ports import RotorPort
from mgm8.infrastructure.mock_rotor import MockRotor
from mgm8.infrastructure.sgp4_pointing import build_pointing_source_factory
from mgm8.rotor_zmq.server import RotorZmqServer

DEFAULT_BIND_ADDRESS = "tcp://127.0.0.1:5580"
DEFAULT_ROTOR_ADDRESS = "tcp://127.0.0.1:5559"
# Espelha tuning_zmq.DEFAULT_TUNING_BIND_ADDRESS sem importar o módulo, que
# arrastaria o pyzmq para dentro de um boot que pode não precisar dele.
DEFAULT_TUNING_BIND_ADDRESS = "tcp://0.0.0.0:5581"


def _build_rotor(rotor_kind: str, rotor_address: str) -> RotorPort:
    if rotor_kind == "mock":
        return MockRotor()
    if rotor_kind == "zmq":
        # Import tardio: pyzmq só precisa estar instalado quando --rotor zmq é usado.
        from mgm8.infrastructure.rot2prog_zmq import Rot2ProgZmqRotor

        return Rot2ProgZmqRotor(rotor_address)
    raise ValueError(f"Tipo de rotor desconhecido: {rotor_kind}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Station Manager: controle de rotor, exposto via ZMQ pro GRS Manager")
    parser.add_argument("--bind", default=DEFAULT_BIND_ADDRESS, help="Endereço ZMQ (REP) onde este serviço escuta")
    parser.add_argument("--rotor", choices=["mock", "zmq"], default="mock", help="Implementação de rotor a usar")
    parser.add_argument("--rotor-address", default=DEFAULT_ROTOR_ADDRESS,
                         help="Endereço ZMQ (PUSH) do RotorManager/simulador (--rotor zmq)")

    # Coordenadas da estação: configuração do processo, não da requisição. Quem
    # manda track_satellite diz qual satélite rastrear, nunca de onde observar.
    # Os defaults vêm do spacelab_tracking, que por sua vez lê variáveis GS_*.
    station = tracking_config.GROUND_STATION
    parser.add_argument("--gs-name", default=station["name"], help="Nome da estação terrestre")
    parser.add_argument("--gs-latitude", type=float, default=station["latitude_deg"],
                         help="Latitude da estação, em graus")
    parser.add_argument("--gs-longitude", type=float, default=station["longitude_deg"],
                         help="Longitude da estação, em graus")
    parser.add_argument("--gs-altitude", type=float, default=station["altitude_m"],
                         help="Altitude da estação, em metros")
    parser.add_argument("--pointing-interval", type=float, default=DEFAULT_UPDATE_INTERVAL_SECONDS,
                         help="Intervalo entre atualizações de apontamento, em segundos. "
                              "Um LEO em passagem alta varia ~1 grau/s no zênite, que é a "
                              "própria resolução do rotor Rot2Prog (1 pulso/grau).")
    parser.add_argument("--pointing-min-elevation", type=float, default=0.0,
                         help="Elevação mínima para comandar o rotor. Abaixo disso o satélite "
                              "está do outro lado da Terra e o azimute varia de forma abrupta. "
                              "Use um valor negativo para exercitar o laço sem esperar uma "
                              "passagem real.")
    parser.add_argument("--tuning-bind", default=None,
                         help="Endereço ZMQ (PUB) onde anunciar frequência e Doppler para o "
                              "grs-frequency-synthesizer. Omitido = não anuncia nada, que é o "
                              "padrão de uma estação sem caminho de recepção. Exemplo: "
                              f"{DEFAULT_TUNING_BIND_ADDRESS}")
    parser.add_argument("--radios", default=os.environ.get("STATION_RADIOS", ""),
                         help="Rádios da estação e a faixa de cada um, em Hz: "
                              "vhf=143000000-148000000,uhf=462000000-470000000. Cada downlink "
                              "do satélite é anunciado no canal do rádio que o cobre "
                              "(freq.vhf, doppler.vhf...). Padrão: a variável STATION_RADIOS. "
                              "Vazio = um canal só, sem nome.")
    args = parser.parse_args()
    radios = parse_radios(args.radios)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(__name__)

    ground_station = {
        "name": args.gs_name,
        "latitude_deg": args.gs_latitude,
        "longitude_deg": args.gs_longitude,
        "altitude_m": args.gs_altitude,
    }

    rotor = _build_rotor(args.rotor, args.rotor_address)
    service = TrackingService(rotor)

    # Import tardio pelo mesmo motivo do rotor ZMQ: pyzmq é dependência
    # opcional do mgm8, e uma estação sem caminho de recepção não deve precisar
    # dela para apontar a antena.
    tuning = None
    if args.tuning_bind:
        from mgm8.infrastructure.tuning_zmq import ZmqTuningBroadcast

        tuning = ZmqTuningBroadcast(args.tuning_bind)

    satellite_tracking = SatelliteTrackingService(
        rotor_control=service,
        pointing_source_factory=build_pointing_source_factory(ground_station),
        update_interval_seconds=args.pointing_interval,
        min_elevation_degrees=args.pointing_min_elevation,
        tuning_broadcast=tuning,
        radios=radios,
        # Doppler para o meio do intervalo entre dois ajustes: metade do erro
        # de dente de serra (ver SatelliteTrackingService).
        doppler_lead_seconds=args.pointing_interval / 2,
    )
    server = RotorZmqServer(args.bind, service, tracking=satellite_tracking)

    logger.info("Station Manager (rotor) escutando em %s (rotor=%s)", args.bind, args.rotor)
    if radios:
        logger.info("Rádios: %s", ", ".join(
            f"{r.name} {r.min_hz / 1e6:g}-{r.max_hz / 1e6:g} MHz" for r in radios))
    logger.info(
        "Estação: %s (%.4f, %.4f, %.0f m) | apontamento a cada %.1fs, elevação mínima %.1f graus",
        ground_station["name"], ground_station["latitude_deg"], ground_station["longitude_deg"],
        ground_station["altitude_m"], args.pointing_interval, args.pointing_min_elevation,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Encerrando por interrupção do usuário.")
    finally:
        if tuning is not None:
            tuning.close()
        satellite_tracking.close()
        server.stop()
        server.close()
        close = getattr(rotor, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
