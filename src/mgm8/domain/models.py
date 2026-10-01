"""Domain entities and value objects for station operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleStatus(StrEnum):
    SCHEDULED = "Scheduled"
    CANCELLED = "Cancelled"
    IN_PROGRESS = "InProgress"
    COMPLETED = "Completed"
    FAILED = "Failed"
    MISSED = "Missed"


class EventSeverity(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class OperationalMode(StrEnum):
    OFFLINE = "Offline"
    INITIALIZING = "Initializing"
    IDLE = "Idle"
    MANUAL = "Manual"
    AUTONOMOUS = "Autonomous"
    PASS_ACTIVE = "PassActive"
    DEGRADED = "Degraded"
    MAINTENANCE = "Maintenance"


class HealthStatus(StrEnum):
    HEALTHY = "Healthy"
    DEGRADED = "Degraded"
    UNHEALTHY = "Unhealthy"
    UNKNOWN = "Unknown"


class ExecutionStatus(StrEnum):
    PENDING = "Pending"
    STARTED = "Started"
    COMPLETED = "Completed"
    FAILED = "Failed"
    ABORTED = "Aborted"


@dataclass(frozen=True)
class PassWindow:
    aos: datetime
    los: datetime

    def __post_init__(self) -> None:
        if self.aos.tzinfo is None or self.los.tzinfo is None:
            raise ValueError("AOS and LOS must include a timezone.")
        if self.los <= self.aos:
            raise ValueError("LOS must be after AOS.")

    def overlaps(self, other: "PassWindow") -> bool:
        return self.aos < other.los and other.aos < self.los


@dataclass(frozen=True)
class FrequencyTune:
    center_frequency_hz: int
    doppler_source: str = "propagator"

    def __post_init__(self) -> None:
        if self.center_frequency_hz <= 0:
            raise ValueError("Frequency must be positive.")
        object.__setattr__(self, "doppler_source", self.doppler_source.strip() or "propagator")


@dataclass(frozen=True)
class AntennaPosition:
    azimuth_degrees: float
    elevation_degrees: float

    def __post_init__(self) -> None:
        if not 0 <= self.azimuth_degrees <= 360:
            raise ValueError("Azimuth must be between 0 and 360 degrees.")
        if not 0 <= self.elevation_degrees <= 90:
            raise ValueError("Elevation must be between 0 and 90 degrees.")


@dataclass(frozen=True)
class SatellitePointing:
    """Onde um satélite está no céu, visto da estação, num dado instante.

    Distinto de AntennaPosition: aqui a elevação pode ser NEGATIVA, porque o
    satélite passa a maior parte do tempo abaixo do horizonte. Só depois de
    decidir que vale apontar é que isso vira uma AntennaPosition, cujos limites
    são os do rotor e não os do céu.
    """

    azimuth_degrees: float
    elevation_degrees: float

    # Desvio Doppler da portadora de descida, em Hz, no instante deste
    # apontamento. Positivo enquanto o satélite se aproxima.
    #
    # None, e não 0.0, quando ninguém disse qual é a frequência do satélite:
    # zero afirmaria "sem desvio", que é falso e mandaria o receptor sintonizar
    # na frequência nominal no meio de uma passagem — justamente onde o desvio
    # é maior. Ausência de dado e ausência de desvio não podem ter a mesma
    # representação.
    doppler_shift_hz: float | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.azimuth_degrees <= 360:
            raise ValueError("Azimuth must be between 0 and 360 degrees.")
        if not -90 <= self.elevation_degrees <= 90:
            raise ValueError("Elevation must be between -90 and 90 degrees.")

    @property
    def is_above_horizon(self) -> bool:
        return self.elevation_degrees > 0.0


@dataclass(frozen=True)
class ApplicationHealth:
    status: HealthStatus
    checked_at: datetime
    latency_ms: int | None = None
    last_error: str | None = None

    @property
    def is_operational(self) -> bool:
        return self.status in {HealthStatus.HEALTHY, HealthStatus.DEGRADED}


@dataclass(frozen=True)
class SchedulingResource:
    name: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Resource name is required.")

    @classmethod
    def rf_chain_default(cls) -> "SchedulingResource":
        return cls("rf_chain")


@dataclass
class ScheduledPass:
    satellite_id: UUID
    window: PassWindow
    frequency: FrequencyTune
    created_by: str | None = None
    auto_execute: bool = True
    max_elevation_degrees: float | None = None
    notes: str | None = None
    id: UUID = field(default_factory=uuid4)
    status: ScheduleStatus = ScheduleStatus.SCHEDULED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def is_active_for_scheduling(self) -> bool:
        return self.status in {ScheduleStatus.SCHEDULED, ScheduleStatus.IN_PROGRESS}

    def overlaps_with(self, other: "ScheduledPass") -> bool:
        return self.is_active_for_scheduling and other.is_active_for_scheduling and self.window.overlaps(other.window)

    def cancel(self, now: datetime | None = None) -> None:
        if self.status in {ScheduleStatus.COMPLETED, ScheduleStatus.IN_PROGRESS}:
            raise ValueError(f"Cannot cancel pass in status '{self.status}'.")
        self.status = ScheduleStatus.CANCELLED
        self.updated_at = now or utc_now()


@dataclass(frozen=True)
class SchedulingConflict:
    pass_a_id: UUID
    pass_b_id: UUID
    resource: str
    id: UUID = field(default_factory=uuid4)
    detected_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.pass_a_id == self.pass_b_id:
            raise ValueError("Conflicting passes must be different.")


@dataclass(frozen=True)
class OperationalEvent:
    severity: EventSeverity
    category: str
    message: str
    satellite_id: UUID | None = None
    pass_id: UUID | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=utc_now)


@dataclass
class ConnectedApplication:
    name: str
    component_type: str
    is_critical: bool
    zmq_address: str | None = None
    version: str | None = None
    description: str | None = None
    id: UUID = field(default_factory=uuid4)
    registered_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass
class ScheduledTelecommand:
    telecommand_definition_id: UUID
    execute_at: datetime
    scheduled_pass_id: UUID | None = None
    priority: int = 5
    requires_approval: bool = False
    created_by: str | None = None
    id: UUID = field(default_factory=uuid4)
    status: ScheduleStatus = ScheduleStatus.SCHEDULED
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def is_ready_for_execution(self) -> bool:
        return self.status is ScheduleStatus.SCHEDULED and (not self.requires_approval or self.approved_at is not None)


@dataclass
class StationState:
    operational_mode: OperationalMode = OperationalMode.OFFLINE
    active_pass_id: UUID | None = None
    active_satellite_id: UUID | None = None
    subsystem_status: dict[str, str] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=utc_now)

    def transition_to(self, mode: OperationalMode, now: datetime | None = None) -> None:
        self.operational_mode = mode
        self.updated_at = now or utc_now()

    def set_active_pass(self, pass_id: UUID, satellite_id: UUID, now: datetime | None = None) -> None:
        self.active_pass_id, self.active_satellite_id = pass_id, satellite_id
        self.transition_to(OperationalMode.PASS_ACTIVE, now)

    def clear_active_pass(self, now: datetime | None = None) -> None:
        self.active_pass_id = self.active_satellite_id = None
        self.transition_to(OperationalMode.IDLE, now)


# --- Downlinks e rádios ---------------------------------------------------------
#
# Um satélite pode descer em mais de uma frequência (o FS-2: beacon em 145,9 MHz
# a 1200 baud e dados em 468,4 MHz a 4800 baud). A estação tem um rádio por
# faixa, todos atrás do MESMO rotor — então os rádios não recebem satélites
# diferentes ao mesmo tempo, e sim frequências diferentes do satélite da vez.
# Cada downlink vai para o rádio cuja faixa o contém.


@dataclass(frozen=True)
class Downlink:
    """Uma portadora de descida do satélite rastreado."""

    name: str
    frequency_hz: float

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise ValueError("downlink sem nome")
        if not self.frequency_hz or self.frequency_hz <= 0:
            raise ValueError(f"downlink {self.name}: frequência inválida {self.frequency_hz}")

    @classmethod
    def from_dict(cls, data: dict) -> "Downlink":
        if not isinstance(data, dict):
            raise ValueError(f"downlink precisa ser um objeto: {data!r}")
        return cls(name=str(data["name"]).strip(), frequency_hz=float(data["frequency_hz"]))


@dataclass(frozen=True)
class RadioBand:
    """Um rádio da estação e a faixa que a cadeia de RF dele cobre (antena,
    LNA, filtros). O nome é o canal dos anúncios de sintonia: `freq.<nome>`."""

    name: str
    min_hz: float
    max_hz: float

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"nome de rádio inválido: {self.name!r}")
        if not 0 < self.min_hz < self.max_hz:
            raise ValueError(f"rádio {self.name}: faixa inválida {self.min_hz}-{self.max_hz}")

    def covers(self, frequency_hz: float) -> bool:
        return self.min_hz <= frequency_hz <= self.max_hz


def parse_radios(text: str) -> list[RadioBand]:
    """`vhf=143000000-148000000,uhf=462000000-470000000` -> rádios.

    Vazio é válido: estação sem rádios declarados anuncia como antes, nos
    tópicos sem canal.
    """
    radios: list[RadioBand] = []
    for item in filter(None, (part.strip() for part in (text or "").split(","))):
        try:
            name, band = item.split("=", 1)
            low, high = band.split("-", 1)
            radios.append(RadioBand(name.strip(), float(low), float(high)))
        except ValueError as error:
            raise ValueError(f"rádio mal escrito {item!r} (esperava nome=min-max em Hz): {error}") from None
    names = [radio.name for radio in radios]
    if len(set(names)) != len(names):
        raise ValueError(f"rádio repetido em {text!r}")
    return radios


@dataclass(frozen=True)
class DownlinkRoute:
    """Um downlink e o rádio que vai ouvi-lo (None = canal sem nome, o modo de
    uma estação sem rádios declarados)."""

    downlink: Downlink
    radio: str | None


def route_downlinks(
    downlinks: list[Downlink], radios: list[RadioBand]
) -> tuple[list[DownlinkRoute], list[str]]:
    """Decide que rádio ouve cada downlink. Devolve (rotas, avisos).

    Sem rádios declarados: só o PRIMEIRO downlink, no canal sem nome — é o
    comportamento de antes, com uma portadora por passagem.

    Com rádios: cada downlink vai ao rádio cuja faixa o contém. Downlink sem
    rádio, ou um segundo downlink na faixa de um rádio já ocupado, fica de fora
    COM aviso: um rádio sintoniza uma frequência por vez.
    """
    if not downlinks:
        return [], []
    if not radios:
        extra = [d.name for d in downlinks[1:]]
        warnings = ([f"sem rádios declarados: só {downlinks[0].name} é anunciado "
                     f"(ignorados: {', '.join(extra)})"] if extra else [])
        return [DownlinkRoute(downlinks[0], None)], warnings

    routes: list[DownlinkRoute] = []
    warnings: list[str] = []
    taken: dict[str, str] = {}
    for downlink in downlinks:
        radio = next((r for r in radios if r.covers(downlink.frequency_hz)), None)
        if radio is None:
            warnings.append(f"{downlink.name} ({downlink.frequency_hz / 1e6:.4f} MHz): "
                            "nenhum rádio da estação cobre essa frequência")
        elif radio.name in taken:
            warnings.append(f"{downlink.name}: o rádio {radio.name} já ouve {taken[radio.name]}")
        else:
            taken[radio.name] = downlink.name
            routes.append(DownlinkRoute(downlink, radio.name))
    return routes, warnings
