"""Composition root do Station Manager (controle de rotor): único módulo que
conhece implementações concretas.

Escolhe o rotor (mock ou ZMQ/Rot2Prog) pela flag --rotor, monta o
TrackingService e expõe via ZMQ REP pro GRS Manager consumir. Nem o núcleo,
nem os adapters, conhecem uns aos outros — só este módulo os amarra.
"""

from __future__ import annotations

import argparse
import logging

from mgm8.application.tracking_service import TrackingService
from mgm8.domain.ports import RotorPort
from mgm8.infrastructure.mock_rotor import MockRotor
from mgm8.rotor_zmq.server import RotorZmqServer

DEFAULT_BIND_ADDRESS = "tcp://127.0.0.1:5580"
DEFAULT_ROTOR_ADDRESS = "tcp://127.0.0.1:5559"


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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(__name__)

    rotor = _build_rotor(args.rotor, args.rotor_address)
    service = TrackingService(rotor)
    server = RotorZmqServer(args.bind, service)

    logger.info("Station Manager (rotor) escutando em %s (rotor=%s)", args.bind, args.rotor)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Encerrando por interrupção do usuário.")
    finally:
        server.stop()
        server.close()
        close = getattr(rotor, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
