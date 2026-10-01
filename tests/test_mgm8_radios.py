"""Testes de mais de um rádio: um downlink por faixa, um canal de sintonia por rádio.

O FS-2 desce em duas portadoras ao mesmo tempo (beacon em 145,9 MHz e dados
em 468,4 MHz), e a estação tem um rádio por faixa, todos atrás do mesmo
rotor. O que se confere aqui é que cada rádio recebe a SUA nominal e o SEU
Doppler — proporcional à portadora —, no canal certo, e que um downlink sem
rádio não vira anúncio num canal errado.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
import zmq

from mgm8.application.satellite_tracking_service import SatelliteTrackingService
from mgm8.domain.models import (
    AntennaPosition,
    Downlink,
    RadioBand,
    SatellitePointing,
    parse_radios,
    route_downlinks,
)

VHF = RadioBand("vhf", 143e6, 148e6)
UHF = RadioBand("uhf", 462e6, 470e6)
BEACON = Downlink("beacon", 145_900_000.0)
DATA = Downlink("dados", 468_400_000.0)


# --- parse_radios -------------------------------------------------------------------


def test_parse_radios_le_a_lista_do_compose():
    radios = parse_radios("vhf=143000000-148000000, uhf=462000000-470000000")

    assert radios == [VHF, UHF]


def test_parse_radios_vazio_e_estacao_de_um_canal():
    assert parse_radios("") == []
    assert parse_radios("  ") == []


@pytest.mark.parametrize("text, message", [
    ("vhf=148000000-143000000", "faixa inválida"),
    ("vhf143000000-148000000", "mal escrito"),
    ("vhf=a-b", "mal escrito"),
    ("v h f=1-2", "nome de rádio inválido"),
    ("vhf=1-2,vhf=3-4", "repetido"),
])
def test_parse_radios_recusa_lista_mal_escrita(text, message):
    with pytest.raises(ValueError, match=message):
        parse_radios(text)


# --- route_downlinks ------------------------------------------------------------------


def test_cada_downlink_vai_ao_radio_da_sua_faixa():
    routes, warnings = route_downlinks([BEACON, DATA], [VHF, UHF])

    assert [(r.downlink.name, r.radio) for r in routes] == [("beacon", "vhf"), ("dados", "uhf")]
    assert warnings == []


def test_sem_radios_so_o_primeiro_downlink_no_canal_sem_nome():
    """O comportamento de antes, de uma portadora por passagem — com aviso
    de quem ficou de fora, para ninguém achar que os dados estão sendo ouvidos."""
    routes, warnings = route_downlinks([BEACON, DATA], [])

    assert [(r.downlink.name, r.radio) for r in routes] == [("beacon", None)]
    assert "dados" in warnings[0]


def test_downlink_sem_radio_fica_de_fora_com_aviso():
    """436 MHz: as antenas UHF desta estação são 398-408 e 462-470 MHz."""
    amateur = Downlink("uhf-amador", 436_100_000.0)

    routes, warnings = route_downlinks([BEACON, amateur], [VHF, UHF])

    assert [r.downlink.name for r in routes] == ["beacon"]
    assert "nenhum rádio" in warnings[0]


def test_dois_downlinks_no_mesmo_radio_fica_o_primeiro():
    """Um rádio sintoniza uma frequência por vez."""
    other = Downlink("beacon-2", 145_800_000.0)

    routes, warnings = route_downlinks([BEACON, other], [VHF])

    assert [r.downlink.name for r in routes] == ["beacon"]
    assert "já ouve beacon" in warnings[0]


def test_downlink_invalido_e_recusado():
    with pytest.raises(ValueError):
        Downlink("x", 0.0)
    with pytest.raises(ValueError):
        Downlink.from_dict({"name": "", "frequency_hz": 145e6})


# --- serviço ---------------------------------------------------------------------------


class ChannelTuning:
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None, float]] = []

    def announce_frequency(self, hz: float, channel: str | None = None) -> None:
        self.events.append(("freq", channel, hz))

    def announce_doppler(self, hz: float, channel: str | None = None) -> None:
        self.events.append(("doppler", channel, hz))


class FakeRotor:
    def set_target(self, azimuth_degrees, elevation_degrees):
        return AntennaPosition(azimuth_degrees, max(elevation_degrees, 0.0))

    def get_position(self):
        return AntennaPosition(0.0, 0.0)

    def stop(self): ...
    def park(self): ...


class RecordingSource:
    """Doppler de referência fixo; registra os instantes pedidos."""

    def __init__(self, reference_doppler: float = 1000.0) -> None:
        self.asked: list[datetime] = []
        self.reference_doppler = reference_doppler

    @property
    def satellite_name(self) -> str:
        return "FS-2"

    def pointing_at(self, when: datetime) -> SatellitePointing:
        self.asked.append(when)
        return SatellitePointing(azimuth_degrees=180.0, elevation_degrees=45.0,
                                 doppler_shift_hz=self.reference_doppler)


def service_with(radios, source, lead=0.0, seen_reference=None):
    def factory(orbital_data, satellite_name=None, frequency=None):
        if seen_reference is not None:
            seen_reference.append(frequency)
        return source

    tuning = ChannelTuning()
    service = SatelliteTrackingService(
        rotor_control=FakeRotor(),
        pointing_source_factory=factory,
        update_interval_seconds=0.02,
        min_elevation_degrees=-90.0,
        park_on_finish=False,
        tuning_broadcast=tuning,
        radios=radios,
        doppler_lead_seconds=lead,
    )
    return service, tuning


def run(service, **kwargs):
    service.start(orbital_data={}, until=datetime.now(timezone.utc) + timedelta(seconds=0.15),
                  satellite_name="FS-2", **kwargs)
    service._thread.join(timeout=3.0)


def test_cada_radio_recebe_sua_nominal_antes_do_seu_doppler():
    service, tuning = service_with([VHF, UHF], RecordingSource())

    run(service, downlinks=[BEACON, DATA])

    first = {}
    for kind, channel, _ in tuning.events:
        first.setdefault((kind, channel), len(first))
    assert first[("freq", "vhf")] < first[("doppler", "vhf")]
    assert first[("freq", "uhf")] < first[("doppler", "uhf")]
    assert ("freq", "vhf", 145_900_000.0) in tuning.events
    assert ("freq", "uhf", 468_400_000.0) in tuning.events


def test_doppler_de_cada_radio_e_proporcional_a_sua_portadora():
    """A propagação calcula o Doppler da portadora de REFERÊNCIA (o primeiro
    downlink roteado); os outros saem por proporção — o desvio é f·v/c."""
    seen = []
    service, tuning = service_with([VHF, UHF], RecordingSource(reference_doppler=1000.0),
                                   seen_reference=seen)

    run(service, downlinks=[BEACON, DATA])

    assert seen == [145_900_000.0]
    vhf = {hz for kind, channel, hz in tuning.events if kind == "doppler" and channel == "vhf"}
    uhf = {hz for kind, channel, hz in tuning.events if kind == "doppler" and channel == "uhf"}
    assert vhf == {1000.0}
    assert len(uhf) == 1 and uhf.pop() == pytest.approx(1000.0 * 468.4 / 145.9)


def test_sem_radios_a_forma_antiga_continua_identica():
    service, tuning = service_with([], RecordingSource())

    run(service, downlink_frequency_hz=145_900_000.0)

    assert {channel for _, channel, _ in tuning.events} == {None}
    assert tuning.events[0] == ("freq", None, 145_900_000.0)


def test_downlink_sem_radio_nao_e_anunciado_em_canal_nenhum():
    service, tuning = service_with([VHF], RecordingSource())

    run(service, downlinks=[BEACON, DATA])

    assert {channel for _, channel, _ in tuning.events} == {"vhf"}


def test_doppler_e_calculado_meio_intervalo_a_frente():
    """O receptor fica com a sintonia de um tick até o próximo; calculada para
    o meio do intervalo, o erro de dente de serra cai pela metade."""
    source = RecordingSource()
    service, _ = service_with([VHF], source, lead=0.5)

    run(service, downlinks=[BEACON])

    # Por tick: o apontamento (agora) e o Doppler (agora + 0,5 s).
    gaps = [(b - a).total_seconds() for a, b in zip(source.asked[::2], source.asked[1::2])]
    assert gaps and all(gap == pytest.approx(0.5, abs=0.01) for gap in gaps)


def test_status_mostra_o_que_cada_radio_ouve():
    service, _ = service_with([VHF, UHF], RecordingSource(reference_doppler=1000.0))
    run(service, downlinks=[BEACON, DATA])

    downlinks = {d["name"]: d for d in service._snapshot().to_dict()["downlinks"]}

    assert downlinks["beacon"]["radio"] == "vhf"
    assert downlinks["dados"]["radio"] == "uhf"
    assert downlinks["dados"]["doppler_hz"] == pytest.approx(1000.0 * 468.4 / 145.9)


# --- adapter ZMQ -------------------------------------------------------------------------


def test_adapter_publica_um_topico_por_canal():
    from mgm8.infrastructure.tuning_zmq import ZmqTuningBroadcast

    broadcast = ZmqTuningBroadcast("tcp://127.0.0.1:0")
    endpoint = broadcast._socket.getsockopt_string(zmq.LAST_ENDPOINT)
    context = zmq.Context()
    sub = context.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, b"freq.uhf")
    sub.setsockopt(zmq.SUBSCRIBE, b"doppler.uhf")
    sub.setsockopt(zmq.RCVTIMEO, 2000)
    sub.connect(endpoint)
    threading.Event().wait(0.3)

    try:
        broadcast.announce_frequency(145_900_000.0, channel="vhf")
        broadcast.announce_doppler(1000.0, channel="vhf")
        broadcast.announce_frequency(468_400_000.0, channel="uhf")
        broadcast.announce_doppler(3210.4, channel="uhf")

        assert sub.recv_multipart() == [b"freq.uhf", b"468400000"]
        assert sub.recv_multipart() == [b"doppler.uhf", b"3210"]
    finally:
        sub.close()
        context.term()
        broadcast.close()


def test_reenvio_da_nominal_e_por_canal():
    """Cada canal conta os próprios Doppler: o reenvio do VHF não pode
    depender de quantos anúncios o UHF fez."""
    from mgm8.infrastructure import tuning_zmq

    sent = []
    broadcast = tuning_zmq.ZmqTuningBroadcast.__new__(tuning_zmq.ZmqTuningBroadcast)
    broadcast._last_frequency_hz = {}
    broadcast._doppler_since_frequency = {}
    broadcast._send = lambda topic, hz: sent.append(topic)

    broadcast.announce_frequency(145_900_000.0, channel="vhf")
    broadcast.announce_frequency(468_400_000.0, channel="uhf")
    for _ in range(tuning_zmq.FREQUENCY_REPEAT_EVERY + 1):
        broadcast.announce_doppler(1.0, channel="vhf")

    assert sent.count(b"freq.vhf") == 2
    assert sent.count(b"freq.uhf") == 1
