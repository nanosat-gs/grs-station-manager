"""Adapter de saída: apontamento de satélite calculado por SGP4.

Envolve a biblioteca `spacelab_tracking`, que faz a propagação orbital e as
transformações de referencial. Este adapter só traduz a porta
SatellitePointingSource para a API dela — não reimplementa nada de mecânica
orbital.

É o que substitui o gpredict como fonte de apontamento: o gpredict entrega o
mesmo cálculo, mas só através da sua interface gráfica, o que impede a estação
de decidir sozinha o que rastrear.

Import de `spacelab_tracking` é de módulo (e não tardio como em
`rot2prog_zmq`): ao contrário do pyzmq, a biblioteca de tracking é instalada
junto com o mgm8 nas imagens da estação.
"""

from __future__ import annotations

from datetime import datetime, timezone

from spacelab_tracking import OrbitalData, build_satellite, get_tracking_info

from mgm8.domain.models import SatellitePointing

UNKNOWN_SATELLITE_NAME = "DESCONHECIDO"


class Sgp4PointingSource:
    """Implementa SatellitePointingSource para um satélite específico.

    O `Satrec` é construído uma vez, no __init__, e reaproveitado a cada
    consulta: propagar é barato, mas montar o objeto a partir do OMM/TLE não
    precisa acontecer a cada tick do laço de apontamento.
    """

    def __init__(
        self,
        orbital_data: dict,
        satellite_name: str | None = None,
        station: dict | None = None,
    ) -> None:
        data = OrbitalData.from_json(orbital_data)
        self._satrec = build_satellite(data)
        self._satellite_name = satellite_name or data.satellite_name or UNKNOWN_SATELLITE_NAME
        self._station = station

    @property
    def satellite_name(self) -> str:
        return self._satellite_name

    def pointing_at(self, when: datetime) -> SatellitePointing:
        if when.tzinfo is None:
            raise ValueError("`when` deve ser timezone-aware (use tzinfo=timezone.utc).")

        info = get_tracking_info(
            self._satrec,
            self._satellite_name,
            when=when.astimezone(timezone.utc),
            station=self._station,
        )
        return SatellitePointing(
            azimuth_degrees=info.topocentric.azimuth_deg,
            elevation_degrees=info.topocentric.elevation_deg,
        )


def build_pointing_source_factory(station: dict | None = None):
    """Devolve uma PointingSourceFactory já amarrada às coordenadas da estação.

    A estação é configuração do processo, não da requisição: quem manda o
    comando `track_satellite` diz qual satélite rastrear, nunca de onde.
    """

    def factory(orbital_data: dict, satellite_name: str | None = None) -> Sgp4PointingSource:
        return Sgp4PointingSource(orbital_data, satellite_name, station)

    return factory
