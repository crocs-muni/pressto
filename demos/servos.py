# demo of Arduino servo pressing via USB serial
#!/usr/bin/env python3
"""
Usage (from another script):
    from servos import press_left, press_right, press_both

    press_left()          # one press on left (servo index 0)
    press_right()         # one press on right (servo index 1)
    press_both()          # both at the same time (Arduino must support 'PA')

Optional:
    set_port("/dev/ttyACM0")  # pin the port if autodetect guesses wrong
"""

import os
import time
from typing import Optional

import serial
from serial.tools import list_ports


BAUD = 115200
LEFT_IDX  = 0
RIGHT_IDX = 1

# Prefer stable by-id symlinks if present, otherwise pick a likely ACM/USB device.
def _guess_port() -> Optional[str]:
    # stable symlinks
    by_id = "/dev/serial/by-id"
    if os.path.isdir(by_id):
        entries = sorted(os.listdir(by_id))
        if entries:
            return os.path.join(by_id, entries[0])

    # look at enumerated ports for ACM/USB
    for p in list_ports.comports():
        if any(tag in (p.device or "") for tag in ("/ttyACM", "/ttyUSB")):
            return p.device

    # common fallbacks
    for dev in ("/dev/ttyACM0", "/dev/ttyUSB0"):
        if os.path.exists(dev):
            return dev
    return None

class _ArduinoPress:
    def __init__(self, port: Optional[str] = None, baud: int = BAUD, wait_ready: bool = True):
        self.port = port or os.environ.get("ARDUINO_PORT") or _guess_port()
        if not self.port:
            raise RuntimeError("No Arduino serial port found. Set ARDUINO_PORT or call set_port().")
        self.baud = baud
        self.ser: Optional[serial.Serial] = None
        self._open(wait_ready=wait_ready)

    def _open(self, wait_ready: bool = True):
        self.ser = serial.Serial(self.port, self.baud, timeout=2)
        # arduino tends to reset on open, wait a bit
        time.sleep(2.0)
        if wait_ready:
            self._drain_until_ready()

    def _drain_until_ready(self, timeout: float = 3.0):
        """Read lines briefly to catch a 'READY' message."""
        t0 = time.time()
        assert self.ser is not None
        self.ser.reset_input_buffer()
        while time.time() - t0 < timeout:
            line = self.ser.readline().decode(errors="ignore").strip()
            if not line:
                continue
            if "READY" in line or "ready" in line:
                break

    def _send_cmd(self, text: str) -> str:
        if not self.ser or not self.ser.is_open:
            self._open(wait_ready=False)
        assert self.ser is not None
        self.ser.write((text + "\n").encode())
        reply = self.ser.readline().decode(errors="ignore").strip()
        return reply

    def press(self, idx: int) -> str:
        return self._send_cmd(f"P{idx}")

    def press_both(self) -> str:
        return self._send_cmd("PA")

    def close(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass

# For easy imports
_controller: Optional[_ArduinoPress] = None

def _get() -> _ArduinoPress:
    global _controller
    if _controller is None:
        _controller = _ArduinoPress()
    return _controller

def set_port(port: str):
    """Optionally call once at startup to pin a specific serial port."""
    global _controller
    if _controller is not None:
        _controller.close()
        _controller = None
    os.environ["ARDUINO_PORT"] = port  # used by _ArduinoPress on next _get()

def press_left() -> str:
    """Press the left servo (index 0). Returns the Arduino's reply string."""
    return _get().press(LEFT_IDX)

def press_right() -> str:
    """Press the right servo (index 1). Returns the Arduino's reply string."""
    return _get().press(RIGHT_IDX)

def press_both() -> str:
    """Press both servos simultaneously (Arduino must support 'PA')."""
    return _get().press_both()

def close():
    """Close the underlying serial port (optional)."""
    global _controller
    if _controller is not None:
        _controller.close()
        _controller = None

# Test
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Press Arduino servos via USB serial.")
    ap.add_argument("--port", help="Override serial port (e.g., /dev/ttyACM0)")
    ap.add_argument("--both", action="store_true", help="Press both servos")
    ap.add_argument("--left", action="store_true", help="Press left servo")
    ap.add_argument("--right", action="store_true", help="Press right servo")
    args = ap.parse_args()

    if args.port:
        set_port(args.port)

    try:
        if args.both:
            print(press_both())
        elif args.left:
            print(press_left())
        elif args.right:
            print(press_right())
        else:
            ap.print_help()
    finally:
        close()