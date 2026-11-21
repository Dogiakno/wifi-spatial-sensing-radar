import argparse
import collections
import glob
import http.server
import json
import math
import random
import socketserver
import threading
import time

try:
    import CoreWLAN
except ImportError:
    CoreWLAN = None

try:
    import serial
except ImportError:
    serial = None

DEFAULT_PORT = 8000


class DualSensorManager:
    def __init__(self, demo_mode=False, serial_port=None):
        self.demo_mode = demo_mode
        self.preferred_serial_port = serial_port

        self.client = None
        self.interface = None
        if not self.demo_mode and CoreWLAN is not None:
            try:
                self.client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
                self.interface = self.client.interface()
            except Exception:
                self.interface = None

        # Zone A (macOS CoreWLAN) state
        self.mac_connected = self.demo_mode or (self.interface is not None)
        self.mac_history = collections.deque(maxlen=60)
        self.mac_score = 0.0
        self.mac_motion = False
        self.mac_rssi = None
        self.fast_rssi = None
        self.slow_rssi = None

        # Zone B (Flipper Zero / ESP32-S3 UART Bridge) state
        self.flipper_connected = self.demo_mode
        self.flipper_port = "SIMULATED" if self.demo_mode else None
        self.flipper_score = 0.0
        self.flipper_motion = False
        self.flipper_rssi = None
        self.flipper_history = collections.deque(maxlen=60)

        if self.demo_mode:
            threading.Thread(target=self._demo_worker, daemon=True).start()
        else:
            threading.Thread(target=self._mac_rssi_poller, daemon=True).start()
            threading.Thread(target=self._flipper_serial_worker, daemon=True).start()

    def _update_mac_sample(self, rssi):
        self.mac_rssi = rssi
        if self.fast_rssi is None:
            self.fast_rssi = rssi
            self.slow_rssi = rssi

        # Fast and slow exponential moving averages
        self.fast_rssi = 0.35 * rssi + 0.65 * self.fast_rssi
        self.slow_rssi = 0.05 * rssi + 0.95 * self.slow_rssi

        delta = abs(self.fast_rssi - self.slow_rssi)
        self.mac_history.append(rssi)

        self.mac_score = round(delta * 2.0, 2)
        self.mac_motion = self.mac_score > 0.8

    def _update_flipper_sample(self, sig):
        self.flipper_rssi = sig
        self.flipper_history.append(sig)
        if len(self.flipper_history) >= 8:
            vals = list(self.flipper_history)[-8:]
            mean = sum(vals) / len(vals)
            variance = sum((x - mean) ** 2 for x in vals) / len(vals)
            self.flipper_score = round(variance, 2)
            self.flipper_motion = self.flipper_score > 1.5

    def _mac_rssi_poller(self):
        """Polls live RSSI fluctuations from macOS CoreWLAN."""
        while True:
            try:
                if self.interface:
                    rssi = float(self.interface.rssiValue())
                    if rssi < 0:
                        self.mac_connected = True
                        self._update_mac_sample(rssi)
            except Exception:
                pass
            time.sleep(0.08)  # 80 ms polling interval

    def _discover_serial_ports(self):
        if self.preferred_serial_port:
            return [self.preferred_serial_port]
        return (
            glob.glob("/dev/tty.usbmodem*")
            + glob.glob("/dev/tty.usbserial*")
            + glob.glob("/dev/ttyACM*")
            + glob.glob("/dev/ttyUSB*")
        )

    def _flipper_serial_worker(self):
        """Listens to RSSI/SIG lines bridged via Flipper Zero USB-CDC."""
        if serial is None:
            return

        while True:
            if not self.flipper_connected:
                ports = self._discover_serial_ports()
                if ports:
                    try:
                        self.flipper_port = ports[0]
                        ser = serial.Serial(self.flipper_port, 115200, timeout=1)
                        self.flipper_connected = True

                        while self.flipper_connected:
                            line = ser.readline().decode("utf-8", errors="ignore").strip()
                            if "RSSI:" in line or "SIG:" in line:
                                parts = line.replace(",", " ").split()
                                for part in parts:
                                    if part.startswith("-") and part[1:].isdigit():
                                        self._update_flipper_sample(float(part))
                    except Exception:
                        self.flipper_connected = False
                        self.flipper_port = None
                        time.sleep(2)
            time.sleep(1)

    def _demo_worker(self):
        """Generates realistic dual-node RF perturbation patterns for hardware-free testing."""
        t = 0.0
        while True:
            phase = int(t // 4.0) % 4  # 0: idle, 1: Zone A, 2: Center, 3: Zone B
            mac_noise = random.uniform(-0.35, 0.35)
            flipper_noise = random.uniform(-0.4, 0.4)

            if phase in (1, 2):
                mac_noise += 4.2 * math.sin(t * 5.5) + random.uniform(-1.5, 1.5)
            if phase in (2, 3):
                flipper_noise += 4.8 * math.cos(t * 6.0) + random.uniform(-1.8, 1.8)

            self._update_mac_sample(round(-52.0 + mac_noise, 2))
            self._update_flipper_sample(round(-58.0 + flipper_noise, 2))

            t += 0.08
            time.sleep(0.08)

    def get_status(self):
        zone = "IDLE"
        if self.mac_motion and self.flipper_motion:
            zone = "CENTER (BETWEEN SENSORS)"
        elif self.mac_motion:
            zone = "ZONE A (NEAR MACBOOK)"
        elif self.flipper_motion:
            zone = "ZONE B (NEAR FLIPPER)"

        return {
            "demo_mode": self.demo_mode,
            "mac_connected": self.mac_connected,
            "mac_rssi": self.mac_rssi,
            "mac_score": self.mac_score,
            "mac_motion": self.mac_motion,
            "mac_history": list(self.mac_history),
            "flipper_connected": self.flipper_connected,
            "flipper_port": self.flipper_port,
            "flipper_rssi": self.flipper_rssi,
            "flipper_score": self.flipper_score,
            "flipper_motion": self.flipper_motion,
            "flipper_history": list(self.flipper_history),
            "zone": zone,
        }


manager = None


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class StreamHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            while True:
                try:
                    payload = json.dumps(manager.get_status())
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError):
                    break
        else:
            super().do_GET()

    def log_message(self, format, *args):
        if self.path != "/api/stream":
            super().log_message(format, *args)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Wi-Fi Spatial Sensing Radar (Dual-Node SSE Server)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"HTTP server port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--serial-port",
        type=str,
        default=None,
        help="Optional explicit Flipper Zero serial port path (e.g. /dev/tty.usbmodem*)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with simulated RF perturbations (no macOS CoreWLAN or Flipper required)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if not args.demo and CoreWLAN is None:
        print(
            "[!] CoreWLAN not found. Install macOS dependencies with:\n"
            "    pip install -r requirements.txt\n"
            "    Or try hardware-free simulation with: python3 server.py --demo"
        )

    manager = DualSensorManager(demo_mode=args.demo, serial_port=args.serial_port)

    mode_label = "DEMO SIMULATION" if args.demo else "LIVE HARDWARE"
    with ThreadingTCPServer(("", args.port), StreamHandler) as httpd:
        print(f"[✓] Wi-Fi Spatial Sensing Radar ({mode_label})")
        print(f"[✓] Dashboard ready at: http://localhost:{args.port}")
        httpd.serve_forever()
