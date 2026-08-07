from datetime import datetime
from uuid import UUID

from flask import Flask, jsonify, request

from mgm8.application.pass_scheduler import PassSchedulerService, SchedulePassRequest
from mgm8.domain.services import SchedulingConflictDetector
from mgm8.infrastructure.in_memory import (InMemoryOperationalEventRepository, InMemoryScheduledPassRepository,
                                             InMemorySchedulingConflictRepository)


def create_app() -> Flask:
    app = Flask(__name__)
    service = PassSchedulerService(InMemoryScheduledPassRepository(), InMemorySchedulingConflictRepository(),
                                   InMemoryOperationalEventRepository(), SchedulingConflictDetector())
    app.config["PASS_SCHEDULER"] = service

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.post("/api/passes")
    def schedule_pass():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON object."), 400
        try:
            result = service.schedule_pass(SchedulePassRequest(
                satellite_id=UUID(payload["satellite_id"]),
                aos=datetime.fromisoformat(payload["aos"]),
                los=datetime.fromisoformat(payload["los"]),
                center_frequency_hz=int(payload["center_frequency_hz"]),
                doppler_source=payload.get("doppler_source"),
                max_elevation_degrees=payload.get("max_elevation_degrees"),
                auto_execute=payload.get("auto_execute", True),
                notes=payload.get("notes"),
                created_by=payload.get("created_by"),
            ))
        except (KeyError, TypeError, ValueError) as error:
            return jsonify(error=str(error)), 400

        if not result.succeeded:
            return jsonify(error="Scheduling conflict.", conflicts=[{
                "id": str(item.id), "pass_a_id": str(item.pass_a_id), "pass_b_id": str(item.pass_b_id),
                "resource": item.resource, "detected_at": item.detected_at.isoformat(),
            } for item in result.conflicts]), 409

        scheduled_pass = result.scheduled_pass
        return jsonify(id=str(scheduled_pass.id), status=scheduled_pass.status.value), 201

    return app


app = create_app()
