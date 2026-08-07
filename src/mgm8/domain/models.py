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
