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
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from mgm8.domain.models import (
    Downlink,
    DownlinkRoute,
    RadioBand,
    SatellitePointing,
    afc_step,
    route_downlinks,
)
from mgm8.domain.ports import (
    NullTuningBroadcast,
    PointingSourceFactory,
    RotorControlUseCase,
    SatellitePointingSource,
    TuningBroadcast,
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
    # O que cada rádio está ouvindo, e o último Doppler anunciado para ele.
    downlinks: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {
            "satellite_name": self.satellite_name,
            "until": self.until.isoformat(),
            "is_pointing": self.is_pointing,
            "azimuth_degrees": self.last_pointing.azimuth_degrees if self.last_pointing else None,
            "elevation_degrees": (
                self.last_pointing.elevation_degrees if self.last_pointing else None
            ),
            "downlinks": list(self.downlinks),
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
        tuning_broadcast: TuningBroadcast | None = None,
        radios: list[RadioBand] | None = None,
        doppler_lead_seconds: float = 0.0,
        afc_gain: float = 0.0,
        afc_max_offset_hz: float = 8000.0,
        afc_max_step_hz: float = 2000.0,
        afc_settle_seconds: float = 1.5,
    ) -> None:
        self._rotor_control = rotor_control
        self._pointing_source_factory = pointing_source_factory
        # NullTuningBroadcast, e não None: rodar sem caminho de RX continua
        # sendo caso de primeira classe, e o laço não testa nada a cada tick.
        self._tuning = tuning_broadcast if tuning_broadcast is not None else NullTuningBroadcast()
        self._update_interval_seconds = update_interval_seconds
        self._min_elevation_degrees = min_elevation_degrees
        self._park_on_finish = park_on_finish
        self._radios = list(radios or [])
        # Para quando o Doppler é calculado, à frente do instante do tick. O
        # receptor fica com a sintonia de um tick até o próximo; calculada
        # para o MEIO desse intervalo (lead = intervalo/2), o erro vai de
        # -½ a +½ intervalo em vez de 0 a 1 — metade do pior caso. Zero
        # mantém a conta no instante do tick.
        self._doppler_lead = timedelta(seconds=doppler_lead_seconds)
        # Ajuste fino pelo bloco FFT. Ganho 0 = desligado: nenhuma medida é
        # aplicada e nenhum `offset` é anunciado (o sintetizador continua
        # somando só nominal + Doppler, como antes).
        self._afc_gain = afc_gain
        self._afc_max_offset = afc_max_offset_hz
        self._afc_max_step = afc_max_step_hz
        # Depois de mudar o desvio, a sintonia nova leva um tick para ser
        # anunciada e o receptor, para mudar; uma medida de rajada que chega
        # nesse meio-tempo foi feita com a sintonia VELHA e seria somada duas
        # vezes. Descarta-se o que chegar dentro deste intervalo.
        self._afc_settle = afc_settle_seconds

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()

        # Estado observável por get_tracking, protegido por _lock.
        self._source: Optional[SatellitePointingSource] = None
        self._until: Optional[datetime] = None
        self._last_pointing: Optional[SatellitePointing] = None
        self._is_pointing = False
        self._routes: list[DownlinkRoute] = []
        self._last_doppler: dict[str, float] = {}
        self._afc: dict[str, dict] = {}

    # --- API pública --------------------------------------------------------

    def start(
        self,
        orbital_data: dict,
        until: datetime,
        satellite_name: str | None = None,
        downlink_frequency_hz: float | None = None,
        downlinks: list[Downlink] | None = None,
    ) -> TrackingStatus:
        """Começa a rastrear até `until`. Substitui um rastreamento em curso.

        `downlinks` são as portadoras do satélite (o FS-2 tem duas). Vêm no
        payload do comando, e não do banco, porque este serviço não conhece o
        Postgres — quem lê o banco é o TC Scheduler, que já monta a ordem de
        rastreamento. `downlink_frequency_hz` é a forma antiga, de uma
        portadora só, e vira o downlink "principal". Sem nenhuma das duas a
        passagem é rastreada igual, só sem anúncio de sintonia.
        """
        if downlinks is None:
            downlinks = ([Downlink("principal", downlink_frequency_hz)]
                         if downlink_frequency_hz is not None else [])
        routes, warnings = route_downlinks(list(downlinks), self._radios)
        for warning in warnings:
            logger.warning("%s: %s", satellite_name or "satélite", warning)
        # A propagação calcula o Doppler de UMA portadora, a de referência; as
        # outras saem por proporção (o desvio é f·v/c, linear na portadora).
        # Assim a fórmula continua tendo um dono só, a spacelab_tracking.
        reference_hz = routes[0].downlink.frequency_hz if routes else None
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
        source = self._pointing_source_factory(orbital_data, satellite_name, reference_hz)

        self._stop_thread()

        # A nominal é anunciada ANTES de a thread começar: o sintetizador
        # recusa Doppler sem frequência de referência, então a ordem entre as
        # duas mensagens é parte do contrato, não detalhe de implementação.
        for route in routes:
            self._announce_frequency(route)

        with self._lock:
            self._source = source
            self._until = until
            self._last_pointing = None
            self._is_pointing = False
            self._routes = routes
            self._last_doppler = {}
            # Cada passagem começa do zero: o erro do oscilador aprendido numa
            # passagem vai ao operador (pelo Scheduler) como sugestão de
            # corrigir a frequência cadastrada, em vez de virar estado
            # escondido aqui dentro.
            self._afc = {route.radio or "": self._new_afc() for route in routes}
            self._cancel = threading.Event()
            self._thread = threading.Thread(
                target=self._run,
                args=(source, until, self._cancel, routes, reference_hz),
                name=f"tracking-{source.satellite_name}",
                daemon=True,
            )
            self._thread.start()

        logger.info(
            "Rastreando %s até %s (a cada %.1fs)%s",
            source.satellite_name, until.isoformat(), self._update_interval_seconds,
            "".join(f" | {r.downlink.name} {r.downlink.frequency_hz / 1e6:.4f} MHz -> "
                    f"{r.radio or 'canal único'}" for r in routes),
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
            self._routes = []
            self._last_doppler = {}
            self._afc = {}

    def on_afc_measurement(self, radio: str, residual_hz: float, snr_db: float = 0.0) -> bool:
        """Uma medida do bloco FFT do rádio `radio`. True se foi aplicada.

        Ignorada fora de passagem, em rádio que esta passagem não usa, com a
        malha desligada, ou logo depois de um ajuste (ver afc_settle).
        """
        if self._afc_gain <= 0:
            return False
        with self._lock:
            state = self._afc.get(radio)
            if self._source is None or state is None:
                return False
            now = time.monotonic()
            state["last_residual_hz"] = residual_hz
            if now - state["changed_at"] < self._afc_settle:
                state["discarded"] += 1
                return False
            previous = state["offset_hz"]
            state["offset_hz"] = afc_step(previous, residual_hz, self._afc_gain,
                                          self._afc_max_offset, self._afc_max_step)
            state["updates"] += 1
            state["changed_at"] = now
        logger.info("Ajuste fino %s: resíduo %+.0f Hz (SNR %.1f dB) -> desvio %+.0f Hz",
                    radio, residual_hz, snr_db, state["offset_hz"])
        return True

    @staticmethod
    def _new_afc() -> dict:
        return {"offset_hz": 0.0, "updates": 0, "discarded": 0,
                "last_residual_hz": None, "changed_at": float("-inf")}

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
            downlinks=tuple(
                {
                    "name": route.downlink.name,
                    "frequency_hz": route.downlink.frequency_hz,
                    "radio": route.radio,
                    "doppler_hz": self._last_doppler.get(route.downlink.name),
                    "offset_hz": self._afc.get(route.radio or "", {}).get("offset_hz", 0.0),
                    "afc_updates": self._afc.get(route.radio or "", {}).get("updates", 0),
                    "afc_last_residual_hz": self._afc.get(route.radio or "", {}).get(
                        "last_residual_hz"),
                }
                for route in self._routes
            ),
        )

    def _announce_frequency(self, route: DownlinkRoute) -> None:
        try:
            if route.radio is None:
                self._tuning.announce_frequency(route.downlink.frequency_hz)
            else:
                self._tuning.announce_frequency(route.downlink.frequency_hz, channel=route.radio)
        except Exception:
            logger.exception("Falha ao anunciar a frequência de %s", route.downlink.name)

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
        routes: list[DownlinkRoute] | None = None,
        reference_hz: float | None = None,
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

                # O Doppler sai da MESMA fonte que acabou de posicionar a
                # antena. Com antecipação, uma segunda propagação meio tick à
                # frente; sem ela, o próprio apontamento do tick.
                doppler_pointing = pointing
                if routes and self._doppler_lead:
                    try:
                        doppler_pointing = source.pointing_at(now + self._doppler_lead)
                    except Exception:
                        logger.exception("Falha ao calcular o Doppler de %s", name)
                reference_doppler = doppler_pointing.doppler_shift_hz
                if routes and reference_doppler is not None and reference_hz:
                    for route in routes:
                        hz = reference_doppler * route.downlink.frequency_hz / reference_hz
                        try:
                            if route.radio is None:
                                self._tuning.announce_doppler(hz)
                            else:
                                self._tuning.announce_doppler(hz, channel=route.radio)
                        except Exception:
                            # Anunciar sintonia é acessório ao apontamento. Um
                            # sintetizador fora do ar não pode custar a passagem.
                            logger.exception("Falha ao anunciar o Doppler de %s", name)
                        with self._lock:
                            self._last_doppler[route.downlink.name] = hz
                            offset = self._afc.get(route.radio or "", {}).get("offset_hz", 0.0)
                        if self._afc_gain > 0:
                            try:
                                if route.radio is None:
                                    self._tuning.announce_offset(offset)
                                else:
                                    self._tuning.announce_offset(offset, channel=route.radio)
                            except Exception:
                                logger.exception("Falha ao anunciar o ajuste de %s", name)

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
