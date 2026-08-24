"""Testes do rastreamento autônomo.

O serviço dirige o rotor a partir de uma thread, então os testes usam um
intervalo de atualização bem curto e esperam por condição (não por sleep fixo),
para não ficarem lentos nem intermitentes.

Nada aqui toca SGP4 nem a rede: a fonte de apontamento é um dublê, porque o que
está sob teste é o laço — quando aponta, quando para, quando recolhe a antena.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from mgm8.application.satellite_tracking_service import (
    MAX_TRACKING_DURATION,
    SatelliteTrackingService,
)
from mgm8.application.tracking_service import TrackingService
from mgm8.domain.models import AntennaPosition, SatellitePointing

TICK = 0.01  # intervalo de apontamento nos testes


class FakeRotor:
    def __init__(self):
        self.position = AntennaPosition(0.0, 0.0)
        self.moves: list[AntennaPosition] = []
        self.parked = False
        self._lock = threading.Lock()

    def move_to(self, position):
        with self._lock:
            self.position = position
            self.moves.append(position)

    def read_position(self):
        with self._lock:
            return self.position

    def stop(self):
        pass

    def park(self):
        self.parked = True

    def move_count(self) -> int:
        with self._lock:
            return len(self.moves)


class FakePointingSource:
    """Devolve azimute crescente para que os setpoints sejam distinguíveis."""

    def __init__(self, name="FAKE-SAT", elevation=45.0):
        self._name = name
        self._elevation = elevation
        self.calls = 0

    @property
    def satellite_name(self):
        return self._name

    def pointing_at(self, when):
        self.calls += 1
        return SatellitePointing(
            azimuth_degrees=min(self.calls * 10.0, 360.0),
            elevation_degrees=self._elevation,
        )


class ExplodingPointingSource:
    satellite_name = "BAD-SAT"

    def pointing_at(self, when):
        raise RuntimeError("TLE velho demais para propagar")


def build_service(source, **kwargs):
    rotor = FakeRotor()
    service = SatelliteTrackingService(
        rotor_control=TrackingService(rotor),
        pointing_source_factory=lambda orbital_data, satellite_name=None: source,
        update_interval_seconds=kwargs.pop("update_interval_seconds", TICK),
        **kwargs,
    )
    return rotor, service


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(TICK)
    return False


def in_future(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


# --- Validação da requisição ------------------------------------------------

def test_rejects_naive_until():
    _, service = build_service(FakePointingSource())
    with pytest.raises(ValueError, match="timezone-aware"):
        service.start({}, until=datetime.now() + timedelta(minutes=5))


def test_rejects_until_in_the_past():
    _, service = build_service(FakePointingSource())
    with pytest.raises(ValueError, match="já passou"):
        service.start({}, until=in_future(-1))


def test_rejects_absurdly_distant_until():
    """Uma passagem dura minutos; um `until` daqui a semanas é erro de unidade,
    e deixaria a thread rastreando indefinidamente."""
    _, service = build_service(FakePointingSource())
    with pytest.raises(ValueError, match="erro de unidade"):
        service.start({}, until=datetime.now(timezone.utc) + MAX_TRACKING_DURATION * 2)


# --- Laço de apontamento ----------------------------------------------------

def test_drives_rotor_with_pointing_from_source():
    source = FakePointingSource(elevation=45.0)
    rotor, service = build_service(source)
    try:
        service.start({}, until=in_future(60))
        assert wait_until(lambda: rotor.move_count() >= 3)
        assert all(m.elevation_degrees == 45.0 for m in rotor.moves)
        # Azimute crescente: são setpoints novos a cada tick, não o mesmo repetido.
        assert rotor.moves[0].azimuth_degrees < rotor.moves[2].azimuth_degrees
    finally:
        service.stop()


def test_status_reports_current_satellite():
    rotor, service = build_service(FakePointingSource(name="ISS (ZARYA)"))
    try:
        service.start({}, until=in_future(60), satellite_name=None)
        # Espera pelo próprio estado observável: ele é publicado depois do
        # set_target, então esperar por rotor.move_count() corre com a thread.
        assert wait_until(lambda: (s := service.status()) is not None and s.is_pointing)

        status = service.status()
        assert status.satellite_name == "ISS (ZARYA)"
        assert status.to_dict()["elevation_degrees"] == 45.0
        assert rotor.move_count() >= 1
    finally:
        service.stop()


def test_status_is_none_when_idle():
    _, service = build_service(FakePointingSource())
    assert service.status() is None


def test_does_not_command_rotor_below_min_elevation():
    """Abaixo do horizonte o satélite está do outro lado da Terra e o azimute
    varia de forma abrupta — seguir isso só castigaria o rotor."""
    source = FakePointingSource(elevation=-30.0)
    rotor, service = build_service(source, min_elevation_degrees=0.0)
    try:
        service.start({}, until=in_future(60))
        assert wait_until(lambda: source.calls >= 3)

        assert rotor.move_count() == 0
        status = service.status()
        assert status is not None and status.is_pointing is False
        # Mas o apontamento calculado continua visível para quem monitora.
        assert status.to_dict()["elevation_degrees"] == -30.0
    finally:
        service.stop()


def test_negative_min_elevation_allows_tracking_below_horizon():
    """Escotilha para exercitar o laço sem esperar uma passagem real."""
    rotor, service = build_service(
        FakePointingSource(elevation=-30.0), min_elevation_degrees=-90.0
    )
    try:
        service.start({}, until=in_future(60))
        assert wait_until(lambda: rotor.move_count() >= 2)
        # O clamp do TrackingService leva a elevação negativa para 0.
        assert all(m.elevation_degrees == 0.0 for m in rotor.moves)
    finally:
        service.stop()


# --- Fim de janela e cancelamento -------------------------------------------

def test_parks_antenna_when_window_ends():
    rotor, service = build_service(FakePointingSource())
    service.start({}, until=in_future(0.05))

    assert wait_until(lambda: rotor.parked)
    assert wait_until(lambda: service.status() is None)


def test_explicit_stop_does_not_park():
    """Um stop explícito costuma preceder outro apontamento; recolher a antena
    jogaria fora a posição que o próximo comando vai querer."""
    rotor, service = build_service(FakePointingSource())
    service.start({}, until=in_future(60))
    assert wait_until(lambda: rotor.move_count() >= 1)

    service.stop()

    assert rotor.parked is False
    assert service.status() is None


def test_stop_is_idempotent():
    _, service = build_service(FakePointingSource())
    service.stop()
    service.stop()
    assert service.status() is None


def test_start_replaces_an_active_tracking():
    """A estação tem um rotor só: aceitar dois rastreamentos seria prometer o
    que o hardware não entrega."""
    first = FakePointingSource(name="SAT-A")
    rotor = FakeRotor()
    sources = {"current": first}
    service = SatelliteTrackingService(
        rotor_control=TrackingService(rotor),
        pointing_source_factory=lambda orbital_data, satellite_name=None: sources["current"],
        update_interval_seconds=TICK,
    )
    try:
        service.start({}, until=in_future(60))
        assert wait_until(lambda: service.status() is not None)
        assert service.status().satellite_name == "SAT-A"

        sources["current"] = FakePointingSource(name="SAT-B")
        service.start({}, until=in_future(60))

        assert wait_until(lambda: service.status().satellite_name == "SAT-B")
        # Substituir não recolhe a antena: o novo alvo assume na sequência.
        assert rotor.parked is False
    finally:
        service.stop()


def test_invalid_orbital_data_does_not_interrupt_active_tracking():
    """Uma requisição malformada não pode derrubar uma passagem em andamento."""
    rotor = FakeRotor()
    good = FakePointingSource(name="SAT-BOM")

    def factory(orbital_data, satellite_name=None):
        if orbital_data.get("broken"):
            raise ValueError("dados orbitais inválidos")
        return good

    service = SatelliteTrackingService(
        rotor_control=TrackingService(rotor),
        pointing_source_factory=factory,
        update_interval_seconds=TICK,
    )
    try:
        service.start({}, until=in_future(60))
        assert wait_until(lambda: rotor.move_count() >= 1)

        with pytest.raises(ValueError, match="inválidos"):
            service.start({"broken": True}, until=in_future(60))

        moves_before = rotor.move_count()
        assert service.status().satellite_name == "SAT-BOM"
        assert wait_until(lambda: rotor.move_count() > moves_before)
    finally:
        service.stop()


def test_propagation_failure_ends_the_pass_instead_of_hanging():
    """Se a propagação falhar, é melhor encerrar e recolher do que deixar o
    rotor apontado para o último setpoint sem ninguém saber."""
    rotor, service = build_service(ExplodingPointingSource())
    service.start({}, until=in_future(60))

    assert wait_until(lambda: rotor.parked)
    assert rotor.move_count() == 0


def test_transient_rotor_failure_does_not_end_the_pass():
    """Perder um setpoint custa um tick de atraso; abortar custa a passagem
    inteira. O protocolo Rot2Prog perde resposta sob carga, então isso
    acontece de verdade."""
    class FlakyRotorControl:
        def __init__(self):
            self.attempts = 0

        def set_target(self, azimuth_degrees, elevation_degrees):
            self.attempts += 1
            if self.attempts <= 2:
                raise RuntimeError("rotor não respondeu a tempo")
            return AntennaPosition(azimuth_degrees, elevation_degrees)

        def get_position(self):
            return AntennaPosition(0.0, 0.0)

        def stop(self):
            pass

        def park(self):
            pass

    rotor_control = FlakyRotorControl()
    service = SatelliteTrackingService(
        rotor_control=rotor_control,
        pointing_source_factory=lambda orbital_data, satellite_name=None: FakePointingSource(),
        update_interval_seconds=TICK,
    )
    try:
        service.start({}, until=in_future(60))
        # Segue tentando depois das duas primeiras falhas, e volta a apontar.
        assert wait_until(lambda: (s := service.status()) is not None and s.is_pointing)
        assert rotor_control.attempts > 2
    finally:
        service.stop()
