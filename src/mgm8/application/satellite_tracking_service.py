"""Caso de uso: rastrear um satélite em tempo real até o fim da passagem.

É o que dá autonomia ao Station Manager. Antes, quem calculava az/el a cada
instante era o gpredict, do lado de fora, empurrando setpoints pelo rotctld —
o que exigia um operador humano na frente da tela. Aqui a estação recebe *uma*
ordem ("rastreie este satélite até o LOS") e conduz a passagem inteira sozinha.

O laço fica deliberadamente deste lado, e não no TC Scheduler:

- planejar é lento (consultar banco, propagar 24h de N satélites) e apontar é
  rápido; misturar os dois num processo faria um replanejamento travar o rotor;
- se o Scheduler cair no meio de uma passagem, o rotor continua acompanhando
  o satélite até o LOS em vez de congelar apontado para o nada;
- não há round-trip de rede por setpoint.

Não fala com o rotor diretamente: usa o RotorControlUseCase (TrackingService),
reaproveitando o clamp de curso e o lock que já existem lá.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from mgm8.domain.models import SatellitePointing
from mgm8.domain.ports import (
    PointingSourceFactory,
    RotorControlUseCase,
    SatellitePointingSource,
)

logger = logging.getLogger(__name__)

DEFAULT_UPDATE_INTERVAL_SECONDS = 1.0

# Elevação mínima para de fato comandar o rotor. Abaixo disso o satélite está
# do outro lado da Terra e o azimute varia de forma abrupta e sem sentido —
# seguir isso só castigaria o hardware. Configurável para permitir testar o
# laço sem esperar uma passagem real.
DEFAULT_MIN_ELEVATION_DEGREES = 0.0

# Guarda contra um `until` absurdo que deixaria a thread rastreando para sempre.
# Uma passagem de LEO dura minutos; 24h é folga de sobra para qualquer órbita.
MAX_TRACKING_DURATION = timedelta(hours=24)


@dataclass(frozen=True)
class TrackingStatus:
    satellite_name: str
    until: datetime
    last_pointing: Optional[SatellitePointing]
    is_pointing: bool  # False quando o satélite está abaixo da elevação mínima

    def to_dict(self) -> dict:
        return {
            "satellite_name": self.satellite_name,
            "until": self.until.isoformat(),
            "is_pointing": self.is_pointing,
            "azimuth_degrees": self.last_pointing.azimuth_degrees if self.last_pointing else None,
            "elevation_degrees": (
                self.last_pointing.elevation_degrees if self.last_pointing else None
            ),
        }


class SatelliteTrackingService:
    """Conduz uma passagem por vez, numa thread dedicada.

    Uma passagem por vez porque a estação tem um rotor só: aceitar duas seria
    prometer o que o hardware não entrega. Um `start` durante um rastreamento
    ativo substitui o anterior — quem decide prioridade é o Scheduler, não aqui.
    """

    def __init__(
        self,
        rotor_control: RotorControlUseCase,
        pointing_source_factory: PointingSourceFactory,
        update_interval_seconds: float = DEFAULT_UPDATE_INTERVAL_SECONDS,
        min_elevation_degrees: float = DEFAULT_MIN_ELEVATION_DEGREES,
        park_on_finish: bool = True,
    ) -> None:
        self._rotor_control = rotor_control
        self._pointing_source_factory = pointing_source_factory
        self._update_interval_seconds = update_interval_seconds
        self._min_elevation_degrees = min_elevation_degrees
        self._park_on_finish = park_on_finish

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()

        # Estado observável por get_tracking, protegido por _lock.
        self._source: Optional[SatellitePointingSource] = None
        self._until: Optional[datetime] = None
        self._last_pointing: Optional[SatellitePointing] = None
        self._is_pointing = False

    # --- API pública --------------------------------------------------------

    def start(
        self,
        orbital_data: dict,
        until: datetime,
        satellite_name: str | None = None,
    ) -> TrackingStatus:
        """Começa a rastrear até `until`. Substitui um rastreamento em curso."""
        if until.tzinfo is None:
            raise ValueError("`until` deve ser timezone-aware (ISO 8601 com fuso).")
        until = until.astimezone(timezone.utc)

        now = datetime.now(timezone.utc)
        if until <= now:
            raise ValueError(f"`until` já passou: {until.isoformat()}")
        if until - now > MAX_TRACKING_DURATION:
            raise ValueError(
                f"`until` está a mais de {MAX_TRACKING_DURATION} no futuro — "
                f"uma passagem dura minutos, isto parece um erro de unidade."
            )

        # Construída fora do lock: montar o Satrec pode falhar com dados
        # orbitais inválidos, e um rastreamento em curso não deve ser
        # interrompido por uma requisição malformada.
        source = self._pointing_source_factory(orbital_data, satellite_name)

        self._stop_thread()

        with self._lock:
            self._source = source
            self._until = until
            self._last_pointing = None
            self._is_pointing = False
            self._cancel = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(source, until, self._cancel),
                name=f"tracking-{source.satellite_name}",
                daemon=True,
            )
            self._thread.start()

        logger.info(
            "Rastreando %s até %s (a cada %.1fs)",
            source.satellite_name, until.isoformat(), self._update_interval_seconds,
        )
        return self._snapshot()

    def stop(self) -> None:
        """Interrompe o rastreamento. Idempotente."""
        self._stop_thread()
        with self._lock:
            self._source = None
            self._until = None
            self._last_pointing = None
            self._is_pointing = False

    def status(self) -> Optional[TrackingStatus]:
        """Estado atual, ou None se nada está sendo rastreado."""
        with self._lock:
            if self._source is None:
                return None
            return self._snapshot_locked()

    def close(self) -> None:
        self._stop_thread()

    # --- Interno ------------------------------------------------------------

    def _snapshot(self) -> TrackingStatus:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> TrackingStatus:
        return TrackingStatus(
            satellite_name=self._source.satellite_name if self._source else "",
            until=self._until or datetime.now(timezone.utc),
            last_pointing=self._last_pointing,
            is_pointing=self._is_pointing,
        )

    def _stop_thread(self) -> None:
        with self._lock:
            thread, cancel = self._thread, self._cancel
            self._thread = None
        if thread is not None and thread.is_alive():
            cancel.set()
            # Espera um pouco mais que um ciclo, para o laço notar o cancelamento
            # e sair antes de devolvermos o controle.
            thread.join(timeout=self._update_interval_seconds + 2.0)

    def _run(
        self,
        source: SatellitePointingSource,
        until: datetime,
        cancel: threading.Event,
    ) -> None:
        name = source.satellite_name
        try:
            while not cancel.is_set():
                now = datetime.now(timezone.utc)
                if now >= until:
                    logger.info("Fim da janela de %s (LOS).", name)
                    break

                try:
                    pointing = source.pointing_at(now)
                except Exception:
                    # Um erro de propagação (ex.: TLE velho demais) não deve
                    # matar a thread em silêncio e deixar o rotor apontado para
                    # o último setpoint sem ninguém saber.
                    logger.exception("Falha ao calcular o apontamento de %s", name)
                    break

                should_point = pointing.elevation_degrees >= self._min_elevation_degrees
                if should_point:
                    try:
                        self._rotor_control.set_target(
                            pointing.azimuth_degrees, pointing.elevation_degrees
                        )
                    except Exception:
                        # Falha de comunicação com o rotor é transiente (o
                        # protocolo Rot2Prog perde resposta sob carga, ver
                        # infrastructure/rot2prog_zmq). Perder um setpoint custa
                        # um tick de atraso; abortar custa a passagem inteira.
                        logger.exception("Falha ao comandar o rotor para %s", name)
                        should_point = False
                else:
                    logger.debug(
                        "%s abaixo da elevação mínima (%.2f < %.2f); mantendo posição.",
                        name, pointing.elevation_degrees, self._min_elevation_degrees,
                    )

                with self._lock:
                    self._last_pointing = pointing
                    self._is_pointing = should_point

                cancel.wait(self._update_interval_seconds)
        finally:
            # Só recolhe a antena se a passagem terminou sozinha. Num stop()
            # explícito, ou numa substituição por outro satélite, recolher
            # jogaria fora o apontamento que o próximo comando vai querer.
            if self._park_on_finish and not cancel.is_set():
                try:
                    self._rotor_control.park()
                except Exception:
                    logger.exception("Falha ao recolher a antena após a passagem de %s", name)

            with self._lock:
                self._is_pointing = False
                if not cancel.is_set():
                    self._source = None
                    self._until = None
