"""Testes dos comandos ZMQ de rastreamento autônomo.

Complementam test_rotor_zmq_server.py, que cobre o apontamento manual. Aqui o
que importa é o contrato de fio entre o TC Scheduler e o Station Manager: se
ele quebrar, a estação para de rastrear sozinha.

A fonte de apontamento é um dublê — SGP4 já é testado na biblioteca.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

zmq = pytest.importorskip("zmq")

from mgm8.application.satellite_tracking_service import SatelliteTrackingService  # noqa: E402
from mgm8.application.tracking_service import TrackingService  # noqa: E402
from mgm8.domain.models import SatellitePointing  # noqa: E402
from mgm8.infrastructure.mock_rotor import MockRotor  # noqa: E402
from mgm8.rotor_zmq.server import RotorZmqServer  # noqa: E402

TICK = 0.01


class FakePointingSource:
    def __init__(self, name="FAKE-SAT"):
        self._name = name

    @property
    def satellite_name(self):
        return self._name

    def pointing_at(self, when):
        return SatellitePointing(azimuth_degrees=123.0, elevation_degrees=45.0)


def in_future(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def client_context():
    context = zmq.Context()
    yield context
    context.term()


def make_client(context, server):
    socket = context.socket(zmq.REQ)
    socket.setsockopt(zmq.RCVTIMEO, 2000)
    socket.setsockopt(zmq.LINGER, 0)
    socket.connect(server.endpoint)
    return socket


def _serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()


@pytest.fixture
def server():
    """Station Manager com rastreamento autônomo habilitado."""
    tracking = SatelliteTrackingService(
        rotor_control=TrackingService(MockRotor()),
        pointing_source_factory=lambda orbital_data, satellite_name=None: FakePointingSource(
            satellite_name or "FAKE-SAT"
        ),
        update_interval_seconds=TICK,
    )
    instance = RotorZmqServer("tcp://127.0.0.1:0", TrackingService(MockRotor()), tracking=tracking)
    _serve(instance)
    yield instance
    tracking.close()
    instance.stop()
    instance.close()


@pytest.fixture
def server_without_tracking():
    """Station Manager só com apontamento manual — o modo anterior."""
    instance = RotorZmqServer("tcp://127.0.0.1:0", TrackingService(MockRotor()))
    _serve(instance)
    yield instance
    instance.stop()
    instance.close()


def test_track_satellite_starts_and_reports_status(server, client_context):
    client = make_client(client_context, server)
    client.send_json({
        "cmd": "track_satellite",
        "orbital_data": {"source": "tle"},
        "until": in_future(60),
        "satellite_name": "ISS (ZARYA)",
    })

    reply = client.recv_json()

    assert reply["ok"] is True
    assert reply["tracking"]["satellite_name"] == "ISS (ZARYA)"
    assert "until" in reply["tracking"]


def test_get_tracking_reports_live_pointing(server, client_context):
    client = make_client(client_context, server)
    client.send_json({
        "cmd": "track_satellite",
        "orbital_data": {"source": "tle"},
        "until": in_future(60),
    })
    client.recv_json()

    # O apontamento aparece a partir do primeiro tick do laço.
    deadline = time.monotonic() + 3.0
    tracking = None
    while time.monotonic() < deadline:
        client.send_json({"cmd": "get_tracking"})
        tracking = client.recv_json()["tracking"]
        if tracking and tracking["is_pointing"]:
            break
        time.sleep(TICK)

    assert tracking is not None
    assert tracking["azimuth_degrees"] == 123.0
    assert tracking["elevation_degrees"] == 45.0


def test_get_tracking_is_null_when_idle(server, client_context):
    client = make_client(client_context, server)
    client.send_json({"cmd": "get_tracking"})

    assert client.recv_json() == {"ok": True, "tracking": None}


def test_stop_tracking_clears_status(server, client_context):
    client = make_client(client_context, server)
    client.send_json({
        "cmd": "track_satellite",
        "orbital_data": {"source": "tle"},
        "until": in_future(60),
    })
    client.recv_json()

    client.send_json({"cmd": "stop_tracking"})
    assert client.recv_json() == {"ok": True}

    client.send_json({"cmd": "get_tracking"})
    assert client.recv_json()["tracking"] is None


def test_until_without_timezone_is_rejected(server, client_context):
    """Sem fuso, o instante seria lido como hora local do container e o
    rastreamento terminaria na hora errada, sem erro visível."""
    client = make_client(client_context, server)
    client.send_json({
        "cmd": "track_satellite",
        "orbital_data": {"source": "tle"},
        "until": "2030-01-01T12:00:00",
    })

    reply = client.recv_json()

    assert reply["ok"] is False
    assert "fuso" in reply["error"]


@pytest.mark.parametrize(
    "until, expected",
    [
        ("2020-01-01T00:00:00+00:00", "já passou"),
        (12345, "string ISO 8601"),
        ("nao-e-uma-data", "Invalid isoformat"),
    ],
)
def test_invalid_until_returns_error_without_crashing(server, client_context, until, expected):
    client = make_client(client_context, server)
    client.send_json({
        "cmd": "track_satellite",
        "orbital_data": {"source": "tle"},
        "until": until,
    })

    reply = client.recv_json()

    assert reply["ok"] is False
    assert expected in reply["error"]

    # E o servidor continua atendendo.
    client.send_json({"cmd": "get_tracking"})
    assert client.recv_json()["ok"] is True


def test_missing_orbital_data_returns_error(server, client_context):
    client = make_client(client_context, server)
    client.send_json({"cmd": "track_satellite", "until": in_future(60)})

    assert client.recv_json()["ok"] is False


@pytest.mark.parametrize("command", ["track_satellite", "stop_tracking", "get_tracking"])
def test_tracking_commands_report_when_feature_is_disabled(
    server_without_tracking, client_context, command
):
    """Sem o serviço de rastreamento, o apontamento manual segue funcionando e
    só os comandos autônomos recusam — com uma mensagem que explica o motivo."""
    client = make_client(client_context, server_without_tracking)
    client.send_json({"cmd": command, "orbital_data": {}, "until": in_future(60)})

    reply = client.recv_json()

    assert reply["ok"] is False
    assert "não está habilitado" in reply["error"]

    client.send_json({"cmd": "set_target", "azimuth_degrees": 90.0, "elevation_degrees": 20.0})
    assert client.recv_json()["ok"] is True
