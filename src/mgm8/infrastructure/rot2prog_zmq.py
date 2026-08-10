"""Adapter de saída: rotor Rot2Prog físico, comandado via ZMQ.

Envolve a classe `RotorManager` do submódulo vendorizado `vendor/grs-rotor-manager`,
que já implementa a codificação binária do protocolo Rot2Prog e a comunicação ZMQ
(PUSH de comandos / SUB de status, porta 5560 fixa no lado do RotorManager). Este
adapter só traduz a porta RotorPort para a API dessa classe — não reimplementa o
protocolo de baixo nível.

Import de `rotor_manager` é feito só quando este adapter é de fato instanciado (ver
`mgm8.rotctld.main`), para que `pyzmq` continue sendo uma dependência opcional.

LIMITAÇÃO CONHECIDA: `RotorManager.request_status()` não correlaciona pedido e
resposta — cada chamada publica um pedido e lê uma única mensagem pendente do
socket SUB, sem garantir que essa mensagem é a resposta ao pedido que acabou de
ser enviado. Uma resposta atrasada de um pedido anterior pode ficar na fila e
ser entregue como se fosse a mais recente, fazendo `read_position()` retornar
uma posição desatualizada logo após um `move_to`/`park`. Confirmado em teste
manual contra `rotor_simulator.py`. Aceito por ora (sem mitigação); a correção
correta seria no próprio `grs-rotor-manager` (ex.: incluir um número de
sequência na resposta de status).

Consequência prática observada com o gpredict real: com o "Cycle" do rotor
configurado em 10ms (padrão do gpredict), o volume de pedidos de status supera
a capacidade do RotorManager de responder em ordem, e a conexão rotctld fica
instável (reconecta em loop, painel de posição nunca preenche). Configurar
Cycle >= 20ms no gpredict resolve. Ver README, seção "Bridge rotctld".
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_VENDOR_ROOT = Path(__file__).resolve().parents[3] / "vendor" / "grs-rotor-manager"
if str(_VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_VENDOR_ROOT))

from rotor_manager import RotorManager  # noqa: E402

from mgm8.domain.models import AntennaPosition

# Mitiga o "slow joiner" do ZMQ PUB/SUB descrito no README do grs-rotor-manager:
# as primeiras mensagens de status podem ser perdidas antes da inscrição SUB
# ser efetivada no lado do RotorManager.
STARTUP_DELAY_SECONDS = 0.2

STATUS_POLL_INTERVAL_SECONDS = 0.05
STATUS_POLL_TIMEOUT_SECONDS = 2.0

# O protocolo Rot2Prog não tem um comando dedicado de "park"; recolhemos para
# uma posição de repouso conhecida usando o próprio comando de set_position.
PARK_POSITION = AntennaPosition(0.0, 0.0)


class Rot2ProgZmqRotor:
    """Implementa RotorPort delegando à RotorManager (protocolo Rot2Prog via ZMQ)."""

    def __init__(self, rotor_address: str) -> None:
        self._manager = RotorManager(rotor_address)
        time.sleep(STARTUP_DELAY_SECONDS)

    def move_to(self, position: AntennaPosition) -> None:
        self._manager.set_position(position.azimuth_degrees, position.elevation_degrees)

    def read_position(self) -> AntennaPosition:
        # request_status() é não bloqueante e pode retornar None se a resposta
        # ainda não chegou; fazemos polling curto até o timeout.
        deadline = time.monotonic() + STATUS_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            status = self._manager.request_status()
            if status is not None:
                azimuth, elevation = status
                return AntennaPosition(azimuth, elevation)
            time.sleep(STATUS_POLL_INTERVAL_SECONDS)
        raise RuntimeError("Rotor não respondeu ao pedido de status a tempo.")

    def stop(self) -> None:
        self._manager.stop()

    def park(self) -> None:
        self.move_to(PARK_POSITION)

    def close(self) -> None:
        self._manager.close()
