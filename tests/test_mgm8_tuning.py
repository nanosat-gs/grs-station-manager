"""Testes do anúncio de sintonia: frequência nominal e Doppler.

Cobrem o comportamento que a estação promete a quem recebe — não a
implementação do socket. O adapter ZMQ tem um teste só, de contrato de
mensagem; o resto usa um duplo em memória, porque o que importa aqui é
*quando* cada anúncio sai, e isso não depende de rede.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mgm8.application.satellite_tracking_service import SatelliteTrackingService
from mgm8.domain.models import AntennaPosition, SatellitePointing
from mgm8.domain.ports import NullTuningBroadcast


class FakeTuning:
    def __init__(self) -> None:
        self.frequencies: list[float] = []
        self.dopplers: list[float] = []
        self.calls: list[str] = []

    def announce_frequency(self, hz: float) -> None:
        self.frequencies.append(hz)
        self.calls.append("freq")

    def announce_doppler(self, hz: float) -> None:
        self.dopplers.append(hz)
        self.calls.append("doppler")


class FakeRotor:
    def __init__(self) -> None:
        self.targets: list[tuple[float, float]] = []

    def set_target(self, azimuth_degrees: float, elevation_degrees: float) -> AntennaPosition:
        self.targets.append((azimuth_degrees, elevation_degrees))
        return AntennaPosition(azimuth_degrees, max(elevation_degrees, 0.0))

    def get_position(self) -> AntennaPosition:
        return AntennaPosition(0.0, 0.0)

    def stop(self) -> None: ...
    def park(self) -> None: ...


class FakeSource:
    """Fonte de apontamento com Doppler roteirizado."""

    def __init__(self, dopplers: list[float | None]) -> None:
        self._dopplers = list(dopplers)
        self._index = 0

    @property
    def satellite_name(self) -> str:
        return "TESTE-1"

    def pointing_at(self, when: datetime) -> SatellitePointing:
        value = self._dopplers[min(self._index, len(self._dopplers) - 1)]
        self._index += 1
        return SatellitePointing(
            azimuth_degrees=180.0, elevation_degrees=45.0, doppler_shift_hz=value
        )


def build_service(tuning, dopplers):
    """Devolve (serviço, rotor) — o rotor é a testemunha de que o laço seguiu."""
    source = FakeSource(dopplers)
    rotor = FakeRotor()
    service = SatelliteTrackingService(
        rotor_control=rotor,
        pointing_source_factory=lambda data, name=None, freq=None: source,
        update_interval_seconds=0.01,
        min_elevation_degrees=-90.0,
        park_on_finish=False,
        tuning_broadcast=tuning,
    )
    return service, rotor


def run_briefly(service, downlink_frequency_hz):
    until = datetime.now(timezone.utc) + timedelta(seconds=0.12)
    service.start(
        orbital_data={},
        until=until,
        satellite_name="TESTE-1",
        downlink_frequency_hz=downlink_frequency_hz,
    )
    service._thread.join(timeout=3.0)


def test_frequencia_nominal_sai_antes_de_qualquer_doppler():
    """O sintetizador recusa Doppler sem referência, então a ordem é contrato."""
    tuning = FakeTuning()
    service, rotor = build_service(tuning, [1200.0, 900.0, 600.0])

    run_briefly(service, downlink_frequency_hz=145_900_000.0)

    assert tuning.calls[0] == "freq"
    assert tuning.frequencies[0] == 145_900_000.0


def test_doppler_e_anunciado_a_cada_tick():
    tuning = FakeTuning()
    service, rotor = build_service(tuning, [1200.0, 900.0, 600.0])

    run_briefly(service, downlink_frequency_hz=145_900_000.0)

    assert len(tuning.dopplers) >= 2
    assert tuning.dopplers[0] == 1200.0


def test_sem_frequencia_nao_anuncia_nada():
    """Rastrear sem saber a portadora continua funcionando — só não sintoniza."""
    tuning = FakeTuning()
    service, rotor = build_service(tuning, [None, None])

    run_briefly(service, downlink_frequency_hz=None)

    assert tuning.calls == []


def test_doppler_ausente_nao_vira_zero():
    """Zero afirmaria 'sem desvio', que é falso, e mandaria sintonizar na
    nominal justamente no meio da passagem, onde o desvio é maior."""
    pointing = SatellitePointing(azimuth_degrees=10.0, elevation_degrees=20.0)

    assert pointing.doppler_shift_hz is None


def test_rastreamento_sobrevive_a_falha_do_anunciador():
    """Um sintetizador fora do ar não pode custar a passagem."""

    class Broken(FakeTuning):
        def announce_doppler(self, hz: float) -> None:
            raise RuntimeError("socket morreu")

    tuning = Broken()
    service, rotor = build_service(tuning, [1200.0, 900.0])

    run_briefly(service, downlink_frequency_hz=145_900_000.0)

    # O rotor é a testemunha: o laço continuou comandando apontamento apesar de
    # o anúncio explodir a cada tick. Uma única posição significaria que a
    # primeira exceção matou a thread.
    assert len(rotor.targets) >= 2


def test_null_broadcast_aceita_tudo_sem_fazer_nada():
    null = NullTuningBroadcast()

    null.announce_frequency(145e6)
    null.announce_doppler(-2000.0)
    null.close()


def test_frequencia_de_descida_invalida_e_recusada():
    from mgm8.infrastructure.sgp4_pointing import Sgp4PointingSource

    with pytest.raises(ValueError, match="frequência de descida"):
        Sgp4PointingSource({}, "X", None, downlink_frequency_hz=-1.0)


def test_adapter_zmq_publica_o_contrato_do_sintetizador():
    """Dois frames: tópico e Hz inteiro em ASCII — o que o sintetizador lê."""
    zmq = pytest.importorskip("zmq")
    from mgm8.infrastructure.tuning_zmq import ZmqTuningBroadcast

    context = zmq.Context()
    subscriber = context.socket(zmq.SUB)
    subscriber.setsockopt_string(zmq.SUBSCRIBE, "")
    subscriber.setsockopt(zmq.RCVTIMEO, 3000)

    broadcast = ZmqTuningBroadcast("tcp://127.0.0.1:35581")
    subscriber.connect("tcp://127.0.0.1:35581")

    # PUB descarta o que publica antes de o assinante concluir a conexão.
    import time

    time.sleep(0.4)

    try:
        broadcast.announce_frequency(145_900_000.0)
        topic, payload = subscriber.recv_multipart()
        assert topic == b"freq"
        assert payload == b"145900000"

        broadcast.announce_doppler(-2345.67)
        topic, payload = subscriber.recv_multipart()
        assert topic == b"doppler"
        # Arredondado para Hz inteiro: é o que o sintetizador consome.
        assert payload == b"-2346"
    finally:
        broadcast.close()
        subscriber.close()
        context.term()
