#!/usr/bin/env python3
"""
PCA9685 servo pressing via I2C (Raspberry Pi)

Usage (from another script):
    from servos_pca import press_left, press_right, press_both

    press_left()
    press_right()
    press_both()

Optional tuning:
    set_channels(left=15, right=12)
    set_angles(left_rest=90, left_press=40, right_rest=90, right_press=45)
    set_pulse_range(min_us=700, max_us=2300)  # if your servos hit stops / buzz

Notes:
- Requires external 5V on PCA V+ for servos.
- Pi GND must be common with PCA GND.
"""

import os
import time
from typing import Optional

import board
import busio
from adafruit_pca9685 import PCA9685




FREQ_HZ = int(os.environ.get("PCA9685_FREQ", "50"))

# Channels (your setup)
LEFT_CH  = int(os.environ.get("PCA_LEFT_CH", "15"))
RIGHT_CH = int(os.environ.get("PCA_RIGHT_CH", "12"))

# Angle pairs (tune per mechanical setup)
LEFT_REST_DEG   = float(os.environ.get("PCA_LEFT_REST", "90"))
LEFT_PRESS_DEG  = float(os.environ.get("PCA_LEFT_PRESS", "40"))
RIGHT_REST_DEG  = float(os.environ.get("PCA_RIGHT_REST", "90"))
RIGHT_PRESS_DEG = float(os.environ.get("PCA_RIGHT_PRESS", "45"))

# Pulse range for 0..180 mapping
MIN_US = int(os.environ.get("PCA_MIN_US", "500"))
MAX_US = int(os.environ.get("PCA_MAX_US", "2500"))

# Motion tuning
STEP_DEG = float(os.environ.get("PCA_STEP_DEG", "2"))
STEP_DELAY = float(os.environ.get("PCA_STEP_DELAY", "0.01"))
PRESS_HOLD = float(os.environ.get("PCA_PRESS_HOLD", "0.20"))


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _angle_to_pulse_us(angle: float) -> int:
    a = _clamp(angle, 0.0, 180.0)
    return int(MIN_US + (a / 180.0) * (MAX_US - MIN_US))


def _pulse_us_to_duty(pulse_us: int) -> int:
    period_us = 1_000_000 / FREQ_HZ
    duty = int((pulse_us / period_us) * 0xFFFF)
    return int(_clamp(duty, 0, 0xFFFF))


class _PCAPress:
    def __init__(self):
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.pca = PCA9685(self.i2c)
        self.pca.frequency = FREQ_HZ

        # Track last angles to allow smooth moves
        self._angle = {}

        # Start at rest positions
        self.set_angle(LEFT_CH, LEFT_REST_DEG)
        self.set_angle(RIGHT_CH, RIGHT_REST_DEG)

    def set_angle(self, ch: int, angle: float):
        pulse = _angle_to_pulse_us(angle)
        self.pca.channels[ch].duty_cycle = _pulse_us_to_duty(pulse)
        self._angle[ch] = angle

    def move_smooth(self, ch: int, target: float):
        start = self._angle.get(ch, target)
        if start == target:
            self.set_angle(ch, target)
            return

        step = STEP_DEG if target > start else -STEP_DEG
        a = start
        while (a < target and step > 0) or (a > target and step < 0):
            self.set_angle(ch, a)
            a += step
            time.sleep(STEP_DELAY)
        self.set_angle(ch, target)

    def press_once(self, ch: int, rest: float, press: float):
        self.move_smooth(ch, press)
        time.sleep(PRESS_HOLD)
        self.move_smooth(ch, rest)

    def press_left(self):
        self.press_once(LEFT_CH, LEFT_REST_DEG, LEFT_PRESS_DEG)
        return "OK"

    def press_right(self):
        self.press_once(RIGHT_CH, RIGHT_REST_DEG, RIGHT_PRESS_DEG)
        return "OK"

    def press_both(self):
        # Move to press (near-simultaneous)
        self.move_smooth(LEFT_CH, LEFT_PRESS_DEG)
        self.move_smooth(RIGHT_CH, RIGHT_PRESS_DEG)
        time.sleep(PRESS_HOLD)
        # Return to rest
        self.move_smooth(LEFT_CH, LEFT_REST_DEG)
        self.move_smooth(RIGHT_CH, RIGHT_REST_DEG)
        return "OK"

    def close(self):
        # Return to rest before shutdown
        try:
            self.set_angle(LEFT_CH, LEFT_REST_DEG)
            self.set_angle(RIGHT_CH, RIGHT_REST_DEG)
        except Exception:
            pass
        try:
            self.pca.deinit()
        except Exception:
            pass


_controller: Optional[_PCAPress] = None


def _get() -> _PCAPress:
    global _controller
    if _controller is None:
        _controller = _PCAPress()
    return _controller



def set_channels(left: int, right: int):
    global LEFT_CH, RIGHT_CH, _controller
    LEFT_CH, RIGHT_CH = int(left), int(right)
    if _controller is not None:
        _controller.close()
        _controller = None


def set_angles(*, left_rest: float, left_press: float, right_rest: float, right_press: float):
    global LEFT_REST_DEG, LEFT_PRESS_DEG, RIGHT_REST_DEG, RIGHT_PRESS_DEG, _controller
    LEFT_REST_DEG, LEFT_PRESS_DEG = float(left_rest), float(left_press)
    RIGHT_REST_DEG, RIGHT_PRESS_DEG = float(right_rest), float(right_press)
    if _controller is not None:
        _controller.close()
        _controller = None


def set_pulse_range(*, min_us: int, max_us: int):
    global MIN_US, MAX_US, _controller
    MIN_US, MAX_US = int(min_us), int(max_us)
    if _controller is not None:
        _controller.close()
        _controller = None


# -------- Public API (same names as Arduino version) --------

def press_left() -> str:
    return _get().press_left()

def press_right() -> str:
    return _get().press_right()

def press_both() -> str:
    return _get().press_both()

def close():
    global _controller
    if _controller is not None:
        _controller.close()
        _controller = None


# Test
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Press PCA9685 servos via I2C.")
    ap.add_argument("--both", action="store_true", help="Press both servos")
    ap.add_argument("--left", action="store_true", help="Press left servo")
    ap.add_argument("--right", action="store_true", help="Press right servo")
    args = ap.parse_args()

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
