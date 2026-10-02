"""Testes do ajuste fino da sintonia (AFC): a malha que fecha com o bloco FFT.

O Doppler previsto não vê o erro do oscilador do satélite. O bloco FFT de
cada rádio mede onde a rajada chegou; aqui se confere que essa medida só
mexe na sintonia quando deve (há passagem, o rádio é desta passagem, a
sintonia já assentou), que mexe com moderação (ganho e limites), e que o
desvio sai no canal certo, separado do Doppler.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import zmq

from mgm8.application.satellite_tracking_service import SatelliteTrackingService
from mgm8.domain.models import (
    AntennaPosition,
    Downlink,
    RadioBand,
    SatellitePointing,
    afc_step,
    parse_sources,
)

VHF = RadioBand("vhf", 143e6, 148e6)
UHF = RadioBand("uhf", 462e6, 470e6)
BEACON = Downlink("beacon", 145_900_000.0)
DATA = Downlink("dados", 468_400_000.0)


# --- o passo da malha ---------------------------------------------------------------


def test_passo_anda_uma_fracao_do_residuo():
    assert afc_step(0.0, 1000.0, 0.5, 8000, 2000) == 500.0
    assert afc_step(500.0, 500.0, 0.5, 8000, 2000) == 750.0


def test_passo_respeita_o_limite_por_medida_e_o_total():
    assert afc_step(0.0, 10_000.0, 0.5, 8000, 2000) == 2000.0
    assert afc_step(7500.0, 4000.0, 0.5, 8000, 2000) == 8000.0
    assert afc_step(-7900.0, -4000.0, 0.5, 8000, 2000) == -8000.0


def test_malha_converge_para_o_erro_do_oscilador():
    """Oscilador 1200 Hz acima: a cada rajada o resíduo é o que falta."""
    offset = 0.0
    for _ in range(10):
        offset = afc_step(offset, 1200.0 - offset, 0.5, 8000, 2000)

    assert offset == pytest.approx(1200.0, abs=2)


def test_parse_sources():
    assert parse_sources("vhf=tcp://172.30.0.24:5582, uhf=tcp://172.30.0.34:5582") == {
        "vhf": "tcp://172.30.0.24:5582", "uhf": "tcp://172.30.0.34:5582"}
    assert parse_sources("") == {}
    for bad in ("vhf", "vhf=172.30.0.24:5582", "vhf=tcp://a:1,vhf=tcp://b:2"):
        with pytest.raises(ValueError):
            parse_sources(bad)


# --- o serviço --------------------------------------------------------------------------


class ChannelTuning:
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None, float]] = []

    def announce_frequency(self, hz, channel=None):
        self.events.append(("freq", channel, hz))

    def announce_doppler(self, hz, channel=None):
        self.events.append(("doppler", channel, hz))

    def announce_offset(self, hz, channel=None):
        self.events.append(("offset", channel, hz))


class FakeRotor:
    def set_target(self, azimuth_degrees, elevation_degrees):
        return AntennaPosition(azimuth_degrees, max(elevation_degrees, 0.0))

    def get_position(self):
        return AntennaPosition(0.0, 0.0)

    def stop(self): ...
    def park(self): ...


class Source:
    satellite_name = "FS-2"

    def pointing_at(self, when):
        return SatellitePointing(azimuth_degrees=180.0, elevation_degrees=45.0,
                                 doppler_shift_hz=1000.0)


def service(gain=0.5, settle=0.0, interval=0.02):
    tuning = ChannelTuning()
    svc = SatelliteTrackingService(
        rotor_control=FakeRotor(),
        pointing_source_factory=lambda data, name=None, freq=None: Source(),
        update_interval_seconds=interval,
        min_elevation_degrees=-90.0,
        park_on_finish=False,
        tuning_broadcast=tuning,
        radios=[VHF, UHF],
        afc_gain=gain,
        afc_settle_seconds=settle,
    )
    return svc, tuning


def start(svc, seconds=5.0):
    svc.start(orbital_data={}, until=datetime.now(timezone.utc) + timedelta(seconds=seconds),
              satellite_name="FS-2", downlinks=[BEACON, DATA])


def offsets(tuning, channel):
    return [hz for kind, ch, hz in tuning.events if kind == "offset" and ch == channel]


def test_medida_durante_a_passagem_vira_desvio_no_canal_do_radio():
    svc, tuning = service()
    start(svc)
    try:
        assert svc.on_afc_measurement("vhf", 1200.0, 18.0) is True
        time.sleep(0.1)
    finally:
        svc.stop()

    assert offsets(tuning, "vhf")[-1] == 600.0
    assert set(offsets(tuning, "uhf")) == {0.0}
    status = {d["name"]: d for d in svc._snapshot().to_dict()["downlinks"]} if svc.status() else None
    assert status is None  # parado: sem status


def test_status_mostra_o_desvio_de_cada_radio():
    svc, _ = service()
    start(svc)
    try:
        svc.on_afc_measurement("uhf", -3000.0)
        downlinks = {d["name"]: d for d in svc.status().to_dict()["downlinks"]}
    finally:
        svc.stop()

    assert downlinks["dados"]["offset_hz"] == -1500.0
    assert downlinks["dados"]["afc_updates"] == 1
    assert downlinks["dados"]["afc_last_residual_hz"] == -3000.0
    assert downlinks["beacon"]["offset_hz"] == 0.0


def test_fora_de_passagem_a_medida_e_ignorada():
    svc, _ = service()

    assert svc.on_afc_measurement("vhf", 1200.0) is False


def test_radio_que_a_passagem_nao_usa_e_ignorado():
    svc, _ = service()
    start(svc)
    try:
        assert svc.on_afc_measurement("sband", 1200.0) is False
    finally:
        svc.stop()


def test_malha_desligada_nao_aplica_nem_anuncia():
    svc, tuning = service(gain=0.0)
    start(svc, seconds=0.15)
    assert svc.on_afc_measurement("vhf", 1200.0) is False
    svc._thread.join(timeout=3)

    assert offsets(tuning, "vhf") == []


def test_medida_logo_depois_de_um_ajuste_e_descartada():
    """Feita com a sintonia velha: somada de novo, o ajuste passaria do ponto."""
    svc, _ = service(settle=10.0)
    start(svc)
    try:
        assert svc.on_afc_measurement("vhf", 1200.0) is True
        assert svc.on_afc_measurement("vhf", 1200.0) is False
        status = {d["name"]: d for d in svc.status().to_dict()["downlinks"]}
    finally:
        svc.stop()

    assert status["beacon"]["offset_hz"] == 600.0


def test_cada_passagem_comeca_do_zero():
    svc, _ = service()
    start(svc)
    svc.on_afc_measurement("vhf", 1200.0)
    start(svc)
    try:
        downlinks = {d["name"]: d for d in svc.status().to_dict()["downlinks"]}
    finally:
        svc.stop()

    assert downlinks["beacon"]["offset_hz"] == 0.0


# --- adapters ZMQ -------------------------------------------------------------------------


def test_anunciador_publica_offset_no_canal():
    from mgm8.infrastructure import tuning_zmq

    sent = []
    broadcast = tuning_zmq.ZmqTuningBroadcast.__new__(tuning_zmq.ZmqTuningBroadcast)
    broadcast._send = lambda topic, hz: sent.append((topic, hz))

    broadcast.announce_offset(812.4, channel="vhf")
    broadcast.announce_offset(-5.0)

    assert sent == [(b"offset.vhf", 812.4), (b"offset", -5.0)]


def test_listener_entrega_a_medida_e_ignora_lixo():
    from mgm8.infrastructure.afc_zmq import AfcListener

    got = []
    listener = AfcListener({}, lambda radio, hz, snr: got.append((radio, hz, snr)))
    listener.handle([b"afc.vhf", json.dumps({"offset_hz": 812.4, "snr_db": 17.2}).encode()])
    listener.handle([b"afc.vhf", b"nao e json"])
    listener.handle([b"afc.vhf", json.dumps({"snr_db": 1}).encode()])
    listener.handle([b"fft.vhf", b"{}", b""])

    assert got == [("vhf", 812.4, 17.2)]


def test_listener_assina_as_fontes_por_zmq():
    from mgm8.infrastructure.afc_zmq import AfcListener

    context = zmq.Context()
    fft = context.socket(zmq.PUB)
    port = fft.bind_to_random_port("tcp://127.0.0.1")
    got = threading.Event()
    seen = []
    listener = AfcListener({"uhf": f"tcp://127.0.0.1:{port}"},
                           lambda radio, hz, snr: (seen.append((radio, hz)), got.set()))
    listener.start()
    try:
        deadline = time.time() + 3
        while not got.is_set() and time.time() < deadline:
            fft.send_multipart([b"fft.uhf", b"{}", b"\x00"])  # outro tópico: ignorado
            fft.send_multipart([b"afc.uhf", json.dumps({"offset_hz": -3000}).encode()])
            got.wait(0.1)
    finally:
        listener.stop()
        listener.join(timeout=3)
        fft.close()
        context.term()

    assert seen[0] == ("uhf", -3000.0)
