"""Núcleo de aplicação: implementa RotorControlUseCase usando RotorPort.

Não conhece o protocolo rotctld nem ZMQ — só a porta de saída. Faz o clamp de
curso (limites operacionais deste rotor) antes de repassar qualquer comando.
AntennaPosition já garante os limites físicos genéricos (0-360 / 0-90); as
constantes abaixo existem à parte para permitir apertar essa faixa conforme
o hardware real, sem tocar no value object do domínio.
"""

from __future__ import annotations

import logging
import threading

from mgm8.domain.models import AntennaPosition
from mgm8.domain.ports import RotorPort

logger = logging.getLogger(__name__)

AZ_MIN_DEGREES = 0.0
AZ_MAX_DEGREES = 360.0
EL_MIN_DEGREES = 0.0
EL_MAX_DEGREES = 90.0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


class TrackingService:
    """Implementa a porta de entrada; usa um lock por ser acessada por várias threads."""

    def __init__(self, rotor: RotorPort) -> None:
        self._rotor = rotor
        self._lock = threading.Lock()

    def set_target(self, azimuth_degrees: float, elevation_degrees: float) -> AntennaPosition:
        position = AntennaPosition(
            _clamp(azimuth_degrees, AZ_MIN_DEGREES, AZ_MAX_DEGREES),
            _clamp(elevation_degrees, EL_MIN_DEGREES, EL_MAX_DEGREES),
        )
        logger.info("Alvo após clamp (valor real, antes da codificação do rotor): az=%r el=%r",
                     position.azimuth_degrees, position.elevation_degrees)
        with self._lock:
            self._rotor.move_to(position)
        return position

    def get_position(self) -> AntennaPosition:
        with self._lock:
            return self._rotor.read_position()

    def stop(self) -> None:
        with self._lock:
            self._rotor.stop()

    def park(self) -> None:
        with self._lock:
            self._rotor.park()
