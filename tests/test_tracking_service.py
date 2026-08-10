from mgm8.application.tracking_service import (AZ_MAX_DEGREES, AZ_MIN_DEGREES, EL_MAX_DEGREES, EL_MIN_DEGREES,
                                                 TrackingService)
from mgm8.domain.models import AntennaPosition


class FakeRotor:
    def __init__(self):
        self.position = AntennaPosition(0.0, 0.0)
        self.stopped = False
        self.parked = False

    def move_to(self, position):
        self.position = position

    def read_position(self):
        return self.position

    def stop(self):
        self.stopped = True

    def park(self):
        self.parked = True


def test_set_target_forwards_position_within_range():
    rotor = FakeRotor()
    service = TrackingService(rotor)

    result = service.set_target(180.0, 45.0)

    assert result == AntennaPosition(180.0, 45.0)
    assert rotor.position == AntennaPosition(180.0, 45.0)


def test_set_target_clamps_azimuth_and_elevation_to_operational_limits():
    rotor = FakeRotor()
    service = TrackingService(rotor)

    service.set_target(-10.0, 120.0)
    assert rotor.position == AntennaPosition(AZ_MIN_DEGREES, EL_MAX_DEGREES)

    service.set_target(400.0, -5.0)
    assert rotor.position == AntennaPosition(AZ_MAX_DEGREES, EL_MIN_DEGREES)


def test_get_position_reads_from_rotor():
    rotor = FakeRotor()
    rotor.position = AntennaPosition(90.0, 30.0)
    service = TrackingService(rotor)

    assert service.get_position() == AntennaPosition(90.0, 30.0)


def test_stop_and_park_delegate_to_rotor():
    rotor = FakeRotor()
    service = TrackingService(rotor)

    service.stop()
    service.park()

    assert rotor.stopped is True
    assert rotor.parked is True
