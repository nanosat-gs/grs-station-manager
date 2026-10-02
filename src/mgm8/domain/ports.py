from __future__ import annotations

from datetime import datetime
from typing import Protocol

from mgm8.domain.models import AntennaPosition, SatellitePointing


class RotorPort(Protocol):
    """Porta de saída: comanda o rotor físico (ou um substituto de testes)."""

    def move_to(self, position: AntennaPosition) -> None: ...
    def read_position(self) -> AntennaPosition: ...
    def stop(self) -> None: ...
    def park(self) -> None: ...


class RotorControlUseCase(Protocol):
    """Porta de entrada: casos de uso expostos ao adapter rotctld."""

    def set_target(self, azimuth_degrees: float, elevation_degrees: float) -> AntennaPosition: ...
    def get_position(self) -> AntennaPosition: ...
    def stop(self) -> None: ...
    def park(self) -> None: ...


class SatellitePointingSource(Protocol):
    """Porta de saída: de onde vem o apontamento de um satélite ao longo do tempo.

    Uma instância representa UM satélite, já ligado aos seus dados orbitais e à
    estação — assim o domínio não precisa saber que existem SGP4, TLE ou
    CelesTrak, e trocar o modelo de propagação não toca no núcleo.
    """

    @property
    def satellite_name(self) -> str: ...

    def pointing_at(self, when: datetime) -> SatellitePointing: ...


class TuningBroadcast(Protocol):
    """Porta de saída: anuncia a quem recebe em que frequência sintonizar.

    Duas mensagens, e a separação entre elas é o contrato com o
    `grs-frequency-synthesizer`, que soma as duas:

        announce_frequency   a portadora NOMINAL do satélite, uma vez por
                             passagem. Não muda enquanto a passagem dura.
        announce_doppler     o desvio do instante, a cada tick do laço de
                             apontamento.

    Separadas de propósito: quem consome precisa distinguir "mudou de satélite"
    de "o satélite se moveu". Mandar só a soma faria um salto de dezenas de
    megahertz (troca de satélite) e um de alguns quilohertz (Doppler) chegarem
    indistinguíveis, e o receptor não teria como decidir se vale re-sintonizar
    o hardware ou corrigir em software.

    O Station Manager é quem publica porque é ele que já propaga a órbita a
    cada segundo para apontar a antena — o Doppler sai da MESMA conta que o
    azimute e a elevação, e calcular de novo noutro serviço seria duas fontes
    de verdade sobre onde o satélite está.

    `channel` é o rádio (`vhf`, `uhf`...): com ele, os tópicos viram
    `freq.<canal>` e `doppler.<canal>`, e cada rádio tem o seu sintetizador.
    None mantém os tópicos sem canal, de uma estação com um rádio só.
    """

    def announce_frequency(self, hz: float, channel: str | None = None) -> None: ...
    def announce_doppler(self, hz: float, channel: str | None = None) -> None: ...
    # O ajuste fino medido (AFC), separado do Doppler pelo mesmo motivo que o
    # Doppler é separado da nominal: quem consome precisa saber o que mudou.
    def announce_offset(self, hz: float, channel: str | None = None) -> None: ...


class NullTuningBroadcast:
    """TuningBroadcast que não anuncia nada.

    Mora no domínio, e não ao lado do adapter ZMQ, por uma razão prática: é o
    padrão do laço de apontamento, e a camada de aplicação não pode importar
    infraestrutura. Aqui, importá-lo não arrasta o pyzmq — que no mgm8 é
    dependência OPCIONAL, instalada só quando o rotor ZMQ é usado.

    Rodar o Station Manager sem caminho de recepção continua sendo caso de
    primeira classe, e não um modo degradado cheio de `if`.
    """

    def announce_frequency(self, hz: float, channel: str | None = None) -> None:
        pass

    def announce_doppler(self, hz: float, channel: str | None = None) -> None:
        pass

    def announce_offset(self, hz: float, channel: str | None = None) -> None:
        pass

    def close(self) -> None:
        pass


class PointingSourceFactory(Protocol):
    """Constrói uma SatellitePointingSource a partir dos dados orbitais que
    chegaram pela rede.

    Existe para que o adapter de entrada (ZMQ) possa criar a fonte sem importar
    a camada de infraestrutura: a composition root injeta a implementação
    concreta, e o resto do sistema só vê esta assinatura.
    """

    def __call__(
        self,
        orbital_data: dict,
        satellite_name: str | None = None,
        downlink_frequency_hz: float | None = None,
    ) -> SatellitePointingSource: ...
