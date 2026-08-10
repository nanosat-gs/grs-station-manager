"""Rotor fake em memória — prova a ponta rotctld sem hardware conectado."""

from __future__ import annotations

import logging

from mgm8.domain.models import AntennaPosition

logger = logging.getLogger(__name__)


class MockRotor:
    """Implementa RotorPort guardando a posição em memória e logando comandos."""

    def __init__(self) -> None:
        self._position = AntennaPosition(0.0, 0.0)

    def move_to(self, position: AntennaPosition) -> None:
        self._position = position
        logger.info("MockRotor: movendo para az=%.2f el=%.2f", position.azimuth_degrees, position.elevation_degrees)

    def read_position(self) -> AntennaPosition:
        return self._position

    def stop(self) -> None:
        logger.info("MockRotor: parado em az=%.2f el=%.2f", self._position.azimuth_degrees, self._position.elevation_degrees)

    def park(self) -> None:
        logger.info("MockRotor: recolhendo para a posição de repouso")
        self._position = AntennaPosition(0.0, 0.0)
