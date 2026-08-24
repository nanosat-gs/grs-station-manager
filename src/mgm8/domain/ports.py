from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from mgm8.domain.models import (
    AntennaPosition,
    OperationalEvent,
    SatellitePointing,
    ScheduledPass,
    SchedulingConflict,
)


class ScheduledPassRepository(Protocol):
    def get_active_in_window(self, start: datetime, end: datetime) -> list[ScheduledPass]: ...
    def add(self, scheduled_pass: ScheduledPass) -> None: ...


class SchedulingConflictRepository(Protocol):
    def add(self, conflict: SchedulingConflict) -> None: ...


class OperationalEventRepository(Protocol):
    def add(self, event: OperationalEvent) -> None: ...


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


class PointingSourceFactory(Protocol):
    """Constrói uma SatellitePointingSource a partir dos dados orbitais que
    chegaram pela rede.

    Existe para que o adapter de entrada (ZMQ) possa criar a fonte sem importar
    a camada de infraestrutura: a composition root injeta a implementação
    concreta, e o resto do sistema só vê esta assinatura.
    """

    def __call__(
        self, orbital_data: dict, satellite_name: str | None = None
    ) -> SatellitePointingSource: ...
