import zmq
import struct
import time

# Rot2Prog command bytes
CMD_STOP   = 0x0F
CMD_STATUS = 0x1F
CMD_SET    = 0x2F

# Protocol constants
PACKET_START = 0x57  # 'W'
PACKET_END   = 0x20  # ' '
PACKET_SIZE  = 11

class RotorManager:
    def __init__(self, rotor_address):
        self.context = zmq.Context()

        # PUSH socket — sends commands to the rotor
        self.cmd_socket = self.context.socket(zmq.PUSH)
        self.cmd_socket.connect(rotor_address)

        # SUB socket — receives status responses from the rotor
        self.status_socket = self.context.socket(zmq.SUB)
        self.status_socket.connect("tcp://localhost:5560")
        self.status_socket.setsockopt_string(zmq.SUBSCRIBE, "status")

    def _encode_angle(self, angle_deg):
        """
        Encode an angle in degrees to the two-byte Rot2Prog format.
        The protocol adds a 360 degree offset to handle the full rotation range.
        Returns (high_byte, low_byte).
        """
        value = int(angle_deg + 360)
        high  = value // 100
        low   = value %  100
        return high, low

    def _build_packet(self, azimuth, elevation, command):
        """
        Build a 11-byte Rot2Prog command packet.

        Packet structure:
        [START][AZ_H][AZ_L][AZ_PH][AZ_PL][EL_H][EL_L][EL_PH][EL_PL][CMD][END]
        """
        az_h, az_l = self._encode_angle(azimuth)
        el_h, el_l = self._encode_angle(elevation)

        packet = struct.pack("BBBBBBBBBBB",
            PACKET_START,
            az_h, az_l, 0x01, 0x00,   # AZ high, low, pulse high, pulse low
            el_h, el_l, 0x01, 0x00,   # EL high, low, pulse high, pulse low
            command,
            PACKET_END
        )
        return packet

    def set_position(self, azimuth, elevation):
        """Command the rotor to move to a given azimuth and elevation."""
        packet = self._build_packet(azimuth, elevation, CMD_SET)
        self.cmd_socket.send(packet)
        print(f"[Manager] Set position -> AZ: {azimuth:.1f}°  EL: {elevation:.1f}°")

    def stop(self):
        """Command the rotor to stop immediately."""
        packet = self._build_packet(0, 0, CMD_STOP)
        self.cmd_socket.send(packet)
        print("[Manager] Stop command sent")

    def request_status(self):
        """Request the current position from the rotor."""
        packet = self._build_packet(0, 0, CMD_STATUS)
        self.cmd_socket.send(packet)
        print("[Manager] Status request sent")

        try:
            topic, data = self.status_socket.recv_multipart(flags=zmq.NOBLOCK)
            az, el = struct.unpack("ff", data)
            print(f"[Manager] Status response -> AZ: {az:.1f}°  EL: {el:.1f}°")
            return az, el
        except zmq.Again:
            print("[Manager] No status response received")
            return None

    def close(self):
        self.cmd_socket.close()
        self.status_socket.close()
        self.context.term()
