"""Composition root do bridge rotctld: único módulo que conhece implementações concretas.

Escolhe o rotor (mock ou ZMQ) pela flag --rotor, monta o TrackingService e
sobe o servidor rotctld. Nem o núcleo, nem os adapters, conhecem uns aos
outros — só este módulo os amarra.
"""

from __future__ import annotations

import argparse
import logging

from mgm8.application.tracking_service import TrackingService
from mgm8.domain.ports import RotorPort
from mgm8.infrastructure.mock_rotor import MockRotor
from mgm8.rotctld.server import RotctldServer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4533
# Porta PUSH/PULL de comandos do RotorManager (vendor/grs-rotor-manager). A porta de
# status (SUB, 5560) é fixa dentro da própria RotorManager e não é configurável aqui.
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
    parser = argparse.ArgumentParser(description="Ponte rotctld -> rotor físico (Rot2Prog via ZMQ)")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Endereço onde o servidor rotctld escuta")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Porta rotctld (padrão hamlib: 4533)")
    parser.add_argument("--rotor", choices=["mock", "zmq"], default="mock", help="Implementação de rotor a usar")
    parser.add_argument("--rotor-address", default=DEFAULT_ROTOR_ADDRESS,
                         help="Endereço ZMQ (PUSH) do RotorManager/simulador (--rotor zmq)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger = logging.getLogger(__name__)

    rotor = _build_rotor(args.rotor, args.rotor_address)
    service = TrackingService(rotor)
    server = RotctldServer(args.host, args.port, service)

    logger.info("Servindo rotctld em %s:%d (rotor=%s)", args.host, args.port, args.rotor)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Encerrando por interrupção do usuário.")
    finally:
        server.shutdown()
        server.server_close()
        close = getattr(rotor, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
