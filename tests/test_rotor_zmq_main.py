"""Testa a composition root (mgm8.rotor_zmq.main) — especificamente que o
endereço de status do RotorManager (antes fixo em tcp://localhost:5560 dentro
do vendor/rotor_manager.py) chega corretamente até lá a partir da CLI.

mgm8.rotor_zmq.main importa RotorZmqServer no topo do módulo, que por sua vez
importa pyzmq incondicionalmente — então, como nos outros testes deste
repositório que tocam essa árvore de módulos, o pyzmq precisa estar instalado
até para exercitar o caminho --rotor mock.
"""

import pytest

zmq = pytest.importorskip("zmq")

from mgm8.infrastructure.mock_rotor import MockRotor  # noqa: E402
from mgm8.rotor_zmq import main as main_module  # noqa: E402


def test_default_rotor_status_address_preserves_previous_hardcoded_value():
    # Antes da parametrização, o RotorManager conectava sempre em
    # tcp://localhost:5560. O default da CLI precisa continuar apontando para
    # o mesmo lugar, senão quem não passar a flag nova muda de comportamento
    # sem pedir.
    assert main_module.DEFAULT_ROTOR_STATUS_ADDRESS == "tcp://127.0.0.1:5560"


def test_build_rotor_mock_does_not_need_status_address():
    rotor = main_module._build_rotor("mock", "tcp://127.0.0.1:5559", "tcp://127.0.0.1:5560")
    assert isinstance(rotor, MockRotor)


def test_build_rotor_zmq_forwards_status_address(monkeypatch):
    captured = {}

    class FakeRot2ProgZmqRotor:
        def __init__(self, rotor_address, status_address):
            captured["rotor_address"] = rotor_address
            captured["status_address"] = status_address

    monkeypatch.setattr(
        "mgm8.infrastructure.rot2prog_zmq.Rot2ProgZmqRotor", FakeRot2ProgZmqRotor
    )

    main_module._build_rotor("zmq", "tcp://127.0.0.1:5559", "tcp://127.0.0.1:9999")

    assert captured == {
        "rotor_address": "tcp://127.0.0.1:5559",
        "status_address": "tcp://127.0.0.1:9999",
    }


def test_build_rotor_unknown_kind_raises():
    with pytest.raises(ValueError):
        main_module._build_rotor("carrier-pigeon", "tcp://127.0.0.1:5559", "tcp://127.0.0.1:5560")
