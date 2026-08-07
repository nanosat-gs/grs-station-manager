from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from mgm8.domain.models import OperationalEvent, ScheduledPass, SchedulingConflict


class ScheduledPassRepository(Protocol):
    def get_active_in_window(self, start: datetime, end: datetime) -> list[ScheduledPass]: ...
    def add(self, scheduled_pass: ScheduledPass) -> None: ...


class SchedulingConflictRepository(Protocol):
    def add(self, conflict: SchedulingConflict) -> None: ...


class OperationalEventRepository(Protocol):
    def add(self, event: OperationalEvent) -> None: ...
