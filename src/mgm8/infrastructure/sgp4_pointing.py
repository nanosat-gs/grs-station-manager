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
        downlink_frequency_hz: float | None = None,
    ) -> None:
        # A portadora de descida é por PASSAGEM, não por instante: é ela que
        # transforma a taxa de variação da distância (km/s) num desvio em Hz.
        # None quando ninguém disse qual é — e aí o apontamento sai sem
        # Doppler, em vez de sair com um número inventado.
        #
        # Conferida ANTES de propagar a órbita: é a validação barata, e montar
        # o Satrec com dados orbitais ruins mascararia o erro de unidade com um
        # erro de TLE.
        if downlink_frequency_hz is not None and downlink_frequency_hz <= 0:
            raise ValueError(
                f"frequência de descida inválida: {downlink_frequency_hz}"
            )
        self._downlink_frequency_hz = downlink_frequency_hz

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
        # doppler_shift_hz vem da spacelab_tracking, e não de uma conta
        # repetida aqui: a fórmula tem de ter um dono só, ou a estação passa a
        # discordar de si mesma sobre onde o satélite está.
        doppler = (
            info.doppler_shift_hz(self._downlink_frequency_hz)
            if self._downlink_frequency_hz is not None
            else None
        )

        return SatellitePointing(
            azimuth_degrees=info.topocentric.azimuth_deg,
            elevation_degrees=info.topocentric.elevation_deg,
            doppler_shift_hz=doppler,
        )


def build_pointing_source_factory(station: dict | None = None):
    """Devolve uma PointingSourceFactory já amarrada às coordenadas da estação.

    A estação é configuração do processo, não da requisição: quem manda o
    comando `track_satellite` diz qual satélite rastrear, nunca de onde.
    """

    def factory(
        orbital_data: dict,
        satellite_name: str | None = None,
        downlink_frequency_hz: float | None = None,
    ) -> Sgp4PointingSource:
        return Sgp4PointingSource(orbital_data, satellite_name, station, downlink_frequency_hz)

    return factory
