"""Adapters de entrada do bloco FFT: medidas de desvio e espectro.

Os blocos FFT (grs-fft, um por rádio) moram no Station Server, ao lado do
IQ; o Station Manager pode estar noutra máquina. O que atravessa a rede são
estas mensagens leves, e não o IQ:

    [b"afc.<rádio>", JSON]          uma por rajada: o desvio medido
    [b"fft.<rádio>", JSON, float32] o espectro reduzido, para desenhar

`AfcListener` entrega as medidas à malha (SatelliteTrackingService). A
`SpectrumRelay` só repassa o espectro, para o Spectrum Monitor do Control
Desktop conhecer UM endereço, o do Station Manager.

Os dois rodam em threads próprias, com sockets próprios: nada aqui pode
atrasar o laço de apontamento. E cada fonte tem o seu socket SUB: um
endereço que não resolve num SUB com várias conexões foi medido calando o
socket inteiro (ver grs-station, docs/rx-datapath.md).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import zmq

logger = logging.getLogger(__name__)

AfcCallback = Callable[[str, float, float], None]


class AfcListener(threading.Thread):
    def __init__(self, sources: dict[str, str], on_measurement: AfcCallback) -> None:
        super().__init__(daemon=True, name="afc-listener")
        self._sources = dict(sources)
        self._on_measurement = on_measurement
        # NÃO `_stop`: é o nome de um método interno de threading.Thread, e
        # sobrescrevê-lo quebra o join() (TypeError: 'Event' object is not callable).
        self._stopping = threading.Event()
        self._context = zmq.Context()

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        poller = zmq.Poller()
        sockets = []
        for radio, address in self._sources.items():
            socket = self._context.socket(zmq.SUB)
            socket.setsockopt(zmq.LINGER, 0)
            socket.setsockopt(zmq.SUBSCRIBE, f"afc.{radio}".encode())
            socket.connect(address)
            poller.register(socket, zmq.POLLIN)
            sockets.append(socket)
            logger.info("Ajuste fino do rádio %s: medidas de %s", radio, address)
        try:
            while not self._stopping.is_set():
                for socket, _ in poller.poll(500):
                    self.handle(socket.recv_multipart())
        finally:
            for socket in sockets:
                socket.close()
            self._context.term()

    def handle(self, frames: list[bytes]) -> None:
        if len(frames) != 2 or not frames[0].startswith(b"afc."):
            return
        radio = frames[0][4:].decode(errors="replace")
        try:
            data = json.loads(frames[1])
            residual = float(data["offset_hz"])
            snr = float(data.get("snr_db", 0.0))
        except (ValueError, KeyError, TypeError):
            logger.warning("Medida de ajuste ilegível do rádio %s: %r", radio, frames[1][:80])
            return
        try:
            self._on_measurement(radio, residual, snr)
        except Exception:
            logger.exception("Falha ao aplicar a medida do rádio %s", radio)


class SpectrumRelay(threading.Thread):
    """XSUB nas fontes, XPUB no bind: só repassa `fft.*`, quem assina escolhe."""

    def __init__(self, sources: dict[str, str], bind: str) -> None:
        super().__init__(daemon=True, name="spectrum-relay")
        self._sources = dict(sources)
        self._bind = bind
        self._context = zmq.Context()

    def stop(self) -> None:
        # term() derruba o zmq.proxy com ETERM; é o jeito de pará-lo.
        self._context.term()

    def run(self) -> None:
        frontend = self._context.socket(zmq.XSUB)
        backend = self._context.socket(zmq.XPUB)
        for socket in (frontend, backend):
            socket.setsockopt(zmq.LINGER, 0)
        for address in self._sources.values():
            frontend.connect(address)
        backend.bind(self._bind)
        logger.info("Espectro dos rádios %s repassado em %s",
                    ", ".join(self._sources), self._bind)
        try:
            zmq.proxy(frontend, backend)
        except zmq.ContextTerminated:
            pass
        finally:
            frontend.close()
            backend.close()
