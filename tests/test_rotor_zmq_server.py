import threading

import pytest

zmq = pytest.importorskip("zmq")

from mgm8.application.tracking_service import TrackingService  # noqa: E402
from mgm8.infrastructure.mock_rotor import MockRotor  # noqa: E402
from mgm8.rotor_zmq.server import RotorZmqServer  # noqa: E402


class RaisingRotor(MockRotor):
    def move_to(self, position):
        raise RuntimeError("rotor físico indisponível")


def start_server(rotor):
    service = TrackingService(rotor)
    server = RotorZmqServer("tcp://127.0.0.1:0", service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


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


@pytest.fixture
def server():
    instance = start_server(MockRotor())
    yield instance
    instance.stop()
    instance.close()


def test_set_target_returns_clamped_position(server, client_context):
    client = make_client(client_context, server)
    client.send_json({"cmd": "set_target", "azimuth_degrees": 400.0, "elevation_degrees": -5.0})

    reply = client.recv_json()

    assert reply == {"ok": True, "azimuth_degrees": 360.0, "elevation_degrees": 0.0}


def test_get_position_returns_current_position(server, client_context):
    client = make_client(client_context, server)
    client.send_json({"cmd": "set_target", "azimuth_degrees": 90.0, "elevation_degrees": 20.0})
    client.recv_json()
    client.send_json({"cmd": "get_position"})

    reply = client.recv_json()

    assert reply == {"ok": True, "azimuth_degrees": 90.0, "elevation_degrees": 20.0}


def test_stop_acks(server, client_context):
    client = make_client(client_context, server)
    client.send_json({"cmd": "stop"})

    assert client.recv_json() == {"ok": True}


def test_unknown_command_returns_error(server, client_context):
    client = make_client(client_context, server)
    client.send_json({"cmd": "voar"})

    reply = client.recv_json()

    assert reply["ok"] is False
    assert "voar" in reply["error"]


def test_malformed_request_returns_error_without_crashing(server, client_context):
    client = make_client(client_context, server)
    client.send_json({"cmd": "set_target", "azimuth_degrees": "not-a-number", "elevation_degrees": 10.0})

    assert client.recv_json()["ok"] is False


def test_service_exception_returns_error_instead_of_crashing(client_context):
    server = start_server(RaisingRotor())
    try:
        client = make_client(client_context, server)
        client.send_json({"cmd": "set_target", "azimuth_degrees": 10.0, "elevation_degrees": 10.0})
        reply = client.recv_json()
    finally:
        server.stop()
        server.close()

    assert reply["ok"] is False
