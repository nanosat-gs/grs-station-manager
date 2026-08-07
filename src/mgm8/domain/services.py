from collections.abc import Iterable

from mgm8.domain.models import ScheduledPass, SchedulingConflict, SchedulingResource


class SchedulingConflictDetector:
    """Detects passes that compete for the same RF resource."""

    def detect_conflicts(
        self,
        candidate: ScheduledPass,
        existing_passes: Iterable[ScheduledPass],
        resource: SchedulingResource,
    ) -> list[SchedulingConflict]:
        return [
            SchedulingConflict(candidate.id, existing.id, resource.name)
            for existing in existing_passes
            if candidate.overlaps_with(existing)
        ]
