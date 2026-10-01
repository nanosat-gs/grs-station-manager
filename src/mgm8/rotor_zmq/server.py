"""Servidor ZMQ REP: expõe RotorControlUseCase para o GRS Manager.

Este é o lado Station Manager do protocolo próprio GRS Manager <-> Station
Manager (schema documentado também em
`grs_manager.adapters.station_manager_zmq`, que é quem fala do outro lado):

    {"cmd": "set_target", "azimuth_degrees": <float>, "elevation_degrees": <float>}
        -> {"ok": true, "azimuth_degrees": <float>, "elevation_degrees": <float>}
    {"cmd": "get_position"}
        -> {"ok": true, "azimuth_degrees": <float>, "elevation_degrees": <float>}
    {"cmd": "stop"} / {"cmd": "park"}
        -> {"ok": true}
    qualquer comando, em caso de erro:
        -> {"ok": false, "error": "<mensagem>"}

Os comandos acima são de apontamento manual: quem chama decide o az/el, e o
Station Manager só executa. É assim que o gpredict opera, via GRS Manager.

Já os comandos abaixo são de rastreamento autônomo, usados pelo TC Scheduler:
uma única ordem cobre a passagem inteira, e o Station Manager conduz o
apontamento sozinho até o LOS (ver `mgm8.application.satellite_tracking_service`):

    {"cmd": "track_satellite", "orbital_data": {...}, "until": "<ISO 8601>",
     "satellite_name": "<str, opcional>",
     "downlinks": [{"name": "<str>", "frequency_hz": <número>}, ...]   (opcional)
     "downlink_frequency_hz": <número, opcional — forma antiga, um downlink só>}
        -> {"ok": true, "tracking": {...}}
    {"cmd": "stop_tracking"}
        -> {"ok": true}
    {"cmd": "get_tracking"}
        -> {"ok": true, "tracking": {...} | null}

`orbital_data` é o dicionário de `spacelab_tracking.OrbitalData.to_json()`. Vai
o objeto inteiro, e não só as duas linhas de TLE, porque OMM não é convertível
para texto TLE (exige recalcular o checksum) — mandar as linhas descartaria a
fonte de dados preferida.

ZMQ REP é síncrono (uma requisição, uma resposta, nessa ordem) — por isso o
loop principal é single-threaded, ao contrário do RotctldServer (TCP, uma
thread por conexão).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

import zmq

from mgm8.application.satellite_tracking_service import SatelliteTrackingService
from mgm8.domain.models import Downlink
from mgm8.domain.ports import RotorControlUseCase

logger = logging.getLogger(__name__)

# Intervalo de poll pra permitir que serve_forever() reaja a stop() mesmo
# sem requisição chegando (REP.recv() bloqueia indefinidamente por padrão).
POLL_TIMEOUT_MS = 500


class RotorZmqServer:
    def __init__(
        self,
        bind_address: str,
        service: RotorControlUseCase,
        tracking: Optional[SatelliteTrackingService] = None,
    ) -> None:
        self._service = service
        # Opcional: sem ele, o Station Manager continua servindo apontamento
        # manual normalmente, e só os comandos de rastreamento respondem erro.
        self._tracking = tracking
        # Context dedicado (não o Context.instance() compartilhado): evita
        # instabilidade observada no libzmq no Windows quando muitos sockets
        # de contextos/threads diferentes disputam o context global.
        self._context = zmq.Context()
        self._socket = self._context.socket(zmq.REP)
        self._socket.setsockopt(zmq.RCVTIMEO, POLL_TIMEOUT_MS)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(bind_address)
        self._running = False
        # Setado quando ninguém está dentro de serve_forever. close() espera por
        # ele antes de fechar o socket (ver close()).
        self._idle = threading.Event()
        self._idle.set()

    @property
    def endpoint(self) -> str:
        """Endereço efetivamente vinculado (útil quando bind_address usa porta efêmera ':0')."""
        return self._socket.getsockopt_string(zmq.LAST_ENDPOINT)

    def serve_forever(self) -> None:
        self._running = True
        self._idle.clear()
        try:
            while self._running:
                try:
                    request = self._socket.recv_json()
                except zmq.Again:
                    continue
                except zmq.ZMQError:
                    # Socket ou context fechados por fora. Encerra em silêncio
                    # em vez de deixar a exceção subir sem tratamento.
                    return
                self._socket.send_json(self._dispatch(request))
        finally:
            self._idle.set()

    def _dispatch(self, request: object) -> dict[str, object]:
        try:
            if not isinstance(request, dict):
                raise ValueError(f"requisição não é um objeto JSON: {request!r}")
            command = request["cmd"]
            if command == "set_target":
                position = self._service.set_target(
                    float(request["azimuth_degrees"]), float(request["elevation_degrees"]))
                return {"ok": True, "azimuth_degrees": position.azimuth_degrees,
                        "elevation_degrees": position.elevation_degrees}
            if command == "get_position":
                position = self._service.get_position()
                return {"ok": True, "azimuth_degrees": position.azimuth_degrees,
                        "elevation_degrees": position.elevation_degrees}
            if command == "stop":
                self._service.stop()
                return {"ok": True}
            if command == "park":
                self._service.park()
                return {"ok": True}
            if command == "track_satellite":
                status = self._require_tracking().start(
                    orbital_data=request["orbital_data"],
                    until=_parse_until(request["until"]),
                    satellite_name=request.get("satellite_name"),
                    # Opcional: a portadora do satélite vem do banco, que este
                    # serviço não conhece — quem a lê e a manda junto é o TC
                    # Scheduler. Sem ela a passagem é rastreada igual, só sem
                    # anúncio de sintonia.
                    downlink_frequency_hz=request.get("downlink_frequency_hz"),
                    # Cada portadora vai ao rádio cuja faixa a contém; ver
                    # mgm8.domain.models.route_downlinks.
                    downlinks=_parse_downlinks(request.get("downlinks")),
                )
                return {"ok": True, "tracking": status.to_dict()}
            if command == "stop_tracking":
                self._require_tracking().stop()
                return {"ok": True}
            if command == "get_tracking":
                status = self._require_tracking().status()
                return {"ok": True, "tracking": status.to_dict() if status else None}
            return {"ok": False, "error": f"comando desconhecido: {command!r}"}
        except (KeyError, TypeError, ValueError) as error:
            logger.warning("Requisição ZMQ malformada: %r (%s)", request, error)
            return {"ok": False, "error": str(error)}
        except Exception as error:
            logger.exception("Falha ao executar comando: %r", request)
            return {"ok": False, "error": str(error)}

    def _require_tracking(self) -> SatelliteTrackingService:
        if self._tracking is None:
            raise ValueError(
                "rastreamento autônomo não está habilitado neste Station Manager"
            )
        return self._tracking

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        """Para o laço e só então fecha o socket.

        Socket ZMQ não é thread-safe: fechá-lo de outra thread enquanto
        serve_forever está dentro do recv fazia o libzmq ABORTAR o processo
        (assertion, não exceção) — a suíte de testes caía ao acaso, uma vez em
        duas. Aqui o laço é avisado e sai no próximo timeout de recv (500 ms).
        """
        self._running = False
        if not self._idle.wait(timeout=POLL_TIMEOUT_MS / 1000 + 2.0):
            logger.warning("serve_forever não saiu a tempo; fechando o socket mesmo assim")
        self._socket.close()
        self._context.term()


def _parse_until(raw: object) -> datetime:
    """Converte o `until` da requisição, exigindo fuso horário explícito.

    Um instante sem fuso seria interpretado como hora local do container (UTC),
    o que faria o rastreamento terminar na hora errada sem nenhum erro visível.
    """
    if not isinstance(raw, str):
        raise ValueError(f"`until` deve ser uma string ISO 8601, e não {type(raw).__name__}")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"`until` precisa incluir o fuso horário: {raw!r}")
    return parsed


def _parse_downlinks(raw: object) -> list[Downlink] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("downlinks precisa ser uma lista")
    return [Downlink.from_dict(item) for item in raw]
