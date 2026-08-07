from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from mgm8.domain.models import EventSeverity, FrequencyTune, OperationalEvent, PassWindow, ScheduledPass, SchedulingConflict, SchedulingResource
from mgm8.domain.ports import OperationalEventRepository, ScheduledPassRepository, SchedulingConflictRepository
from mgm8.domain.services import SchedulingConflictDetector


@dataclass(frozen=True)
class SchedulePassRequest:
    satellite_id: UUID
    aos: datetime
    los: datetime
    center_frequency_hz: int
    doppler_source: str | None = None
    max_elevation_degrees: float | None = None
    auto_execute: bool = True
    notes: str | None = None
    created_by: str | None = None


@dataclass(frozen=True)
class SchedulePassResult:
    succeeded: bool
    scheduled_pass: ScheduledPass | None = None
    conflicts: list[SchedulingConflict] = field(default_factory=list)


class PassSchedulerService:
    def __init__(self, pass_repository: ScheduledPassRepository, conflict_repository: SchedulingConflictRepository,
                 event_repository: OperationalEventRepository, conflict_detector: SchedulingConflictDetector) -> None:
        self.pass_repository = pass_repository
        self.conflict_repository = conflict_repository
        self.event_repository = event_repository
        self.conflict_detector = conflict_detector

    def schedule_pass(self, request: SchedulePassRequest) -> SchedulePassResult:
        window = PassWindow(request.aos, request.los)
        frequency = FrequencyTune(request.center_frequency_hz, request.doppler_source or "propagator")
        candidate = ScheduledPass(request.satellite_id, window, frequency, request.created_by, request.auto_execute,
                                  request.max_elevation_degrees, request.notes)
        existing = self.pass_repository.get_active_in_window(window.aos, window.los)
        resource = SchedulingResource.rf_chain_default()
        conflicts = self.conflict_detector.detect_conflicts(candidate, existing, resource)

        if conflicts:
            for conflict in conflicts:
                self.conflict_repository.add(conflict)
            self.event_repository.add(OperationalEvent(EventSeverity.WARNING, "scheduling",
                "Pass scheduling rejected due to RF resource conflict.", request.satellite_id, candidate.id,
                {"conflict_count": len(conflicts), "resource": resource.name}))
            return SchedulePassResult(False, conflicts=conflicts)

        self.pass_repository.add(candidate)
        self.event_repository.add(OperationalEvent(EventSeverity.INFO, "scheduling", "Satellite pass scheduled.",
            request.satellite_id, candidate.id, {"aos": window.aos.isoformat(), "los": window.los.isoformat(),
            "frequency_hz": frequency.center_frequency_hz}))
        return SchedulePassResult(True, candidate)
