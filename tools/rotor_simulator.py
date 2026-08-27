import zmq
import struct
import time

# Rot2Prog constants (must match manager)
CMD_STOP   = 0x0F
CMD_STATUS = 0x1F
CMD_SET    = 0x2F

PACKET_START = 0x57
PACKET_END   = 0x20
PACKET_SIZE  = 11

class RotorSimulator:
    def __init__(self):
        self.context = zmq.Context()

        # PULL socket — receives commands from the manager
        self.cmd_socket = self.context.socket(zmq.PULL)
        self.cmd_socket.bind("tcp://*:5559")

        # PUB socket — sends status responses back to the manager
        self.status_socket = self.context.socket(zmq.PUB)
        self.status_socket.bind("tcp://*:5560")

        # Simulated current position
        self.current_az = 0.0
        self.current_el = 0.0

        self._running = False

    def _decode_angle(self, high, low):
        """
        Decode a two-byte Rot2Prog angle back to degrees.
        Reverses the +360 offset applied during encoding.
        """
        return float(high * 100 + low) - 360.0

    def _decode_packet(self, raw):
        """
        Decode a 11-byte Rot2Prog packet.
        Returns (azimuth, elevation, command) or None if malformed.
        """
        if len(raw) != PACKET_SIZE:
            print(f"[Simulator] Malformed packet: expected {PACKET_SIZE} bytes, got {len(raw)}")
            return None

        start, az_h, az_l, az_ph, az_pl, el_h, el_l, el_ph, el_pl, cmd, end = struct.unpack(
            "BBBBBBBBBBB", raw
        )

        if start != PACKET_START or end != PACKET_END:
            print(f"[Simulator] Invalid packet framing: start=0x{start:02X} end=0x{end:02X}")
            return None

        azimuth   = self._decode_angle(az_h, az_l)
        elevation = self._decode_angle(el_h, el_l)

        return azimuth, elevation, cmd

    def _handle_packet(self, raw):
        result = self._decode_packet(raw)
        if result is None:
            return

        azimuth, elevation, cmd = result

        if cmd == CMD_SET:
            self.current_az = azimuth
            self.current_el = elevation
            print(f"[Simulator] SET POSITION -> Azimuth: {azimuth:.1f}°  Elevation: {elevation:.1f}°")

        elif cmd == CMD_STOP:
            print(f"[Simulator] STOP received. Holding at AZ: {self.current_az:.1f}°  EL: {self.current_el:.1f}°")

        elif cmd == CMD_STATUS:
            print(f"[Simulator] STATUS request -> Responding with AZ: {self.current_az:.1f}°  EL: {self.current_el:.1f}°")
            payload = struct.pack("ff", self.current_az, self.current_el)
            self.status_socket.send_multipart([b"status", payload])

        else:
            print(f"[Simulator] Unknown command byte: 0x{cmd:02X}")

    def run(self):
        print("[Simulator] Running, waiting for commands...")
        self._running = True

        try:
            while self._running:
                try:
                    raw = self.cmd_socket.recv(flags=zmq.NOBLOCK)
                    self._handle_packet(raw)
                except zmq.Again:
                    time.sleep(0.01)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        print("[Simulator] Stopping")
        self._running = False
        self.cmd_socket.close()
        self.status_socket.close()
        self.context.term()


if __name__ == "__main__":
    sim = RotorSimulator()
    sim.run()
