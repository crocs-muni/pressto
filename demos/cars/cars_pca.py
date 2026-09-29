#!/usr/bin/env python3
# Two-Cars bot with:
# - HUMAN/BOT modes (+ ARMING delay), auto-restart
# - LED (BCM17) status: OFF/HUMAN, BLINK/ARMING, ON/BOT
# - Button (BCM27): same as 'B' key toggle
# - Servo mapping swap + test taps
# - Sanity view, decision inspector, tripwire, visible bottom (optional shading)
# - Ignore-bottom band (removes cars from detection)
# - Decision line fully adjustable (0.0–1.0)
# - ROI scan: only detect above decision line (+ adjustable margin)
"""The cars demo's bot: PRESSTO plays a two-cars phone game.

A camera watches the phone's screen (Picamera2, or OpenCV camera 0 when
Picamera2 cannot be started). Each frame is rectified with calibration.json,
objects are found by their calibrated colours in four lanes, and two servo
arms on a PCA9685 (I2C, channel 12 = left, channel 15 = right) tap the phone
so that each car switches lanes: towards a good (green) object, away from a
bad (red) one.

Usage, on the Raspberry Pi, in the folder that holds calibration.json
(written by calibrate.py):

    python3 cars_pca.py

Needs a display: the program shows an OpenCV window and reads its keys from
that window. It starts in HUMAN mode, in which the bot does not tap on its
own; B (or the optional button on BCM27) arms it, and 5 s later it taps once
and plays (BOT mode). Q or Esc quits. README.md next to this file lists
every key.
"""

import json, signal, time
from pathlib import Path
import cv2
import numpy as np

# --- Servo control (PCA9685 on I2C; channels 15 and 12) ---
try:
    import board
    import busio
    from adafruit_pca9685 import PCA9685
    _PCA_AVAILABLE = True
except Exception:
    PCA9685 = None
    _PCA_AVAILABLE = False

# Tune these if your button press needs different travel.
SERVO_FREQ_HZ = 50
SERVO_MIN_US  = 500
SERVO_MAX_US  = 2500

# Physical channels on the PCA9685 (as wired):
LEFT_CH  = 12
RIGHT_CH = 15

# Default angles (degrees) for each servo.
# REST = not pressing; PRESS = pressing the button.
LEFT_REST_DEG  = 70
LEFT_PRESS_DEG = 160
RIGHT_REST_DEG  = 150
RIGHT_PRESS_DEG = 90

PRESS_HOLD_S = 0.10

_pca = None
_i2c = None

def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x

def _angle_to_pulse_us(angle_deg: float) -> int:
    a = _clamp(float(angle_deg), 0.0, 180.0)
    return int(SERVO_MIN_US + (a / 180.0) * (SERVO_MAX_US - SERVO_MIN_US))

def _pulse_us_to_duty(pulse_us: int, freq_hz: int) -> int:
    period_us = 1_000_000 / float(freq_hz)
    duty = int((pulse_us / period_us) * 0xFFFF)
    return int(_clamp(duty, 0, 0xFFFF))

def _get_pca():
    global _pca, _i2c
    if _pca is not None:
        return _pca
    if not _PCA_AVAILABLE:
        raise RuntimeError(
            "PCA9685 library not available. Install with: pip3 install adafruit-circuitpython-pca9685"
        )
    _i2c = busio.I2C(board.SCL, board.SDA)
    _pca = PCA9685(_i2c)
    _pca.frequency = SERVO_FREQ_HZ
    # Initialize to REST so we don't slam on first press.
    _set_angle(LEFT_CH, LEFT_REST_DEG)
    _set_angle(RIGHT_CH, RIGHT_REST_DEG)
    return _pca

def _set_angle(channel: int, angle_deg: float):
    pca = _get_pca()
    pulse = _angle_to_pulse_us(angle_deg)
    pca.channels[int(channel)].duty_cycle = _pulse_us_to_duty(pulse, SERVO_FREQ_HZ)

def press_left():
    _set_angle(LEFT_CH, LEFT_PRESS_DEG)
    try:
        time.sleep(PRESS_HOLD_S)
    finally:
        _set_angle(LEFT_CH, LEFT_REST_DEG)   # released even when the hold is interrupted

def press_right():
    _set_angle(RIGHT_CH, RIGHT_PRESS_DEG)
    try:
        time.sleep(PRESS_HOLD_S)
    finally:
        _set_angle(RIGHT_CH, RIGHT_REST_DEG)   # released even when the hold is interrupted

def press_both():
    # Near-simultaneous: set both, hold, then release both.
    try:
        _set_angle(LEFT_CH, LEFT_PRESS_DEG)
        _set_angle(RIGHT_CH, RIGHT_PRESS_DEG)
        time.sleep(PRESS_HOLD_S)
    finally:
        try:
            _set_angle(LEFT_CH, LEFT_REST_DEG)
        finally:
            _set_angle(RIGHT_CH, RIGHT_REST_DEG)

def close():
    global _pca
    if _pca is not None:
        try:
            # Return to REST on exit
            _set_angle(LEFT_CH, LEFT_REST_DEG)
            _set_angle(RIGHT_CH, RIGHT_REST_DEG)
        except Exception:
            pass
        try:
            _pca.deinit()
        except Exception:
            pass
        _pca = None


CALIB_PATH = Path("calibration.json")

# --- GPIO: LED + Button (optional; app runs without them) ---
LED_PIN = 17       # BCM17 for LED (series resistor required)
BUTTON_PIN = 27    # BCM27 for button (other leg to GND; internal pull-up)

try:
    from gpiozero import LED, Button
    _GPIO_AVAILABLE = True
except Exception:
    LED = Button = None
    _GPIO_AVAILABLE = False

# ---------- Camera ----------
class Camera:
    def __init__(self, width=320, height=180):
        self.picam2 = None
        self.cap = None
        self.w, self.h = width, height
        self.fps, self.has_autofocus = None, False
        self.libcamera = None
        self._start_picam2() or self._start_opencv()

    def _start_picam2(self):
        try:
            from picamera2 import Picamera2
            import libcamera
            self.libcamera = libcamera
            self.picam2 = Picamera2()
            for f in (120, 90, 60, 30):
                try:
                    cfg = self.picam2.create_video_configuration(
                        main={"size": (self.w, self.h), "format": "RGB888"},
                        controls={"FrameDurationLimits": (int(1e6/f), int(1e6/f))}
                    )
                    self.picam2.configure(cfg); self.picam2.start(); time.sleep(0.15); self.fps = f; break
                except Exception:
                    try: self.picam2.stop()
                    except Exception: pass
                    self.picam2 = Picamera2()
            try:
                self.picam2.set_controls({"AfMode": self.libcamera.controls.AfModeEnum.Continuous})
                self.has_autofocus = True
            except Exception:
                pass
            return True
        except Exception:
            self.picam2 = None
            return False

    def _start_opencv(self):
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened(): raise RuntimeError("No camera")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.h)
        for f in (120, 90, 60, 30):
            if self.cap.set(cv2.CAP_PROP_FPS, f): self.fps = f; break
        try:
            if hasattr(cv2, "CAP_PROP_AUTOFOCUS"):
                self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1); self.has_autofocus = True
        except Exception:
            pass

    def refocus(self):
        if self.picam2 is not None and self.has_autofocus:
            try: self.picam2.set_controls({"AfTrigger": self.libcamera.controls.AfTrigger.Start})
            except Exception: pass
        elif self.cap is not None and self.has_autofocus:
            try: self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0); time.sleep(0.05); self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
            except Exception: pass

    def read(self):
        if self.picam2 is not None:
            rgb = self.picam2.capture_array()
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, frame = self.cap.read()
        if not ok: raise RuntimeError("Capture failed")
        return frame

    def release(self):
        if self.picam2 is not None:
            try: self.picam2.stop()
            except Exception: pass
        if self.cap is not None:
            self.cap.release()

# ---------- Utils ----------
def apply_preproc(img, pp):
    out = img
    if pp.get("swap_rb"): out = out[..., ::-1]
    rk = int(pp.get("rotate_k", 0))
    if rk: out = np.ascontiguousarray(np.rot90(out, k=rk))
    if pp.get("flip_h"): out = cv2.flip(out, 1)
    if pp.get("flip_v"): out = cv2.flip(out, 0)
    return out

def hsv_mask(img_bgr, ranges):
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    out = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lo, hi in ranges:
        out |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return out

def find_objects(mask, min_area=40):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    objs = []
    for c in contours:
        a = cv2.contourArea(c)
        if a < min_area: continue
        m = cv2.moments(c)
        if m["m00"] == 0: continue
        cx = int(m["m10"] / m["m00"]); cy = int(m["m01"] / m["m00"])
        objs.append((cx, cy, a))
    return objs

# ---------- Bot ----------
class TwoCarsBot:
    def __init__(self, calib):
        self.load_from_calib(calib)
        # Cars spawn in inner lanes
        self.left_lane  = 1
        self.right_lane = 2
        # Timing & margins
        self.last_press_L = 0.0
        self.last_press_R = 0.0
        self.cooldown_s = 0.22
        self.trigger_margin = int(self.H * 0.12)
        self.collect_margin = int(self.H * 0.16)
        self.safety_gap     = int(self.H * 0.14)
        # Servo mapping (False = L->left, R->right; True = swapped)
        self.servo_swap = False

    def load_from_calib(self, calib):
        self.W = int(calib["rectified_size"]["w"])
        self.H = int(calib["rectified_size"]["h"])
        self.warpM = np.array(calib["warp_matrix"], dtype=np.float32)
        self.pp = calib.get("preprocess", {})
        self.lanes = int(calib.get("lanes", 4))
        self.decision_y_frac = float(calib.get("decision_y_frac", 0.80))
        self.car_y_frac = float(calib.get("car_y_frac", 0.88))
        self.good_ranges = calib["good_hsv_ranges"]
        self.bad_ranges  = calib["bad_hsv_ranges"]

    def lane_of_x(self, x):
        return int(np.clip(x * self.lanes // self.W, 0, self.lanes - 1))

    def rect_frame(self, frame):
        frame = apply_preproc(frame, self.pp)
        return cv2.warpPerspective(frame, self.warpM, (self.W, self.H))

    # ---------- Servo helpers ----------
    def tap_side(self, side):
        """Tap the correct physical servo for a logical side ('L' or 'R')."""
        if not self.servo_swap:
            press_left()  if side == "L" else press_right()
        else:
            press_right() if side == "L" else press_left()

    # ---- Decision planning (no side effects) ----
    def plan(self, objs_good, objs_bad, now_s):
        y_dec = int(self.H * self.decision_y_frac)
        nearest_good = [None]*self.lanes
        nearest_bad  = [None]*self.lanes

        def upd(bucket, lane, obj):
            if obj[1] >= y_dec:  # ignore below decision line
                return
            if bucket[lane] is None or obj[1] > bucket[lane][1]:
                bucket[lane] = obj

        for (cx, cy, a) in objs_good:
            upd(nearest_good, self.lane_of_x(cx), (cx, cy, a))
        for (cx, cy, a) in objs_bad:
            upd(nearest_bad,  self.lane_of_x(cx), (cx, cy, a))

        def side_plan(cur_lane, lanes, last_press_time):
            other = lanes[1] if cur_lane == lanes[0] else lanes[0]
            bad_cur   = nearest_bad[cur_lane]
            bad_other = nearest_bad[other]
            good_other= nearest_good[other]
            cd = max(0.0, self.cooldown_s - (now_s - last_press_time))
            will = False; reason = "NONE"
            if cd > 0:
                reason = "COOLDOWN"
            else:
                if bad_cur is not None and bad_cur[1] >= y_dec - self.trigger_margin:
                    if bad_other is not None and bad_other[1] >= y_dec - self.safety_gap:
                        reason = "BLOCKED"
                    else:
                        will = True; reason = "DODGE"
                elif good_other is not None and good_other[1] >= y_dec - self.collect_margin:
                    if bad_other is not None and bad_other[1] >= y_dec - self.safety_gap:
                        reason = "BLOCKED"
                    else:
                        will = True; reason = "COLLECT"
            return {
                "cur": cur_lane, "other": other, "y_dec": y_dec,
                "will_press": will, "reason": reason, "cd_remain": cd,
                "bad_cur": bad_cur, "bad_other": bad_other, "good_other": good_other,
            }

        planL = side_plan(self.left_lane,  (0,1), self.last_press_L)
        planR = side_plan(self.right_lane, (2,3), self.last_press_R)
        return {"L": planL, "R": planR}

    def execute_plan(self, plan, now_s):
        if plan["L"]["will_press"] and plan["L"]["cd_remain"] <= 0:
            self.tap_side("L")
            self.left_lane = 1 if self.left_lane == 0 else 0
            self.last_press_L = now_s
            print(f"[act] LEFT  -> toggle ({plan['L']['reason']})")
        if plan["R"]["will_press"] and plan["R"]["cd_remain"] <= 0:
            self.tap_side("R")
            self.right_lane = 3 if self.right_lane == 2 else 2
            self.last_press_R = now_s
            print(f"[act] RIGHT -> toggle ({plan['R']['reason']})")

    def sync_to_game_start(self):
        """Align our internal lane memory with the game's spawn state (both cars center)."""
        self.left_lane, self.right_lane = 1, 2
        self.last_press_L = self.last_press_R = 0.0

# ---------- Helpers for overlay/debug ----------
def lane_center_x(lane, W, lanes):
    x0 = int(lane * W / lanes)
    x1 = int((lane+1) * W / lanes)
    return (x0 + x1)//2

def nearest_per_lane(objs, W, lanes):
    buckets = [None]*lanes
    for (cx, cy, a) in objs:
        lane = int(np.clip(cx * lanes // W, 0, lanes-1))
        if buckets[lane] is None or cy > buckets[lane][1]:
            buckets[lane] = (cx, cy, a)
    return buckets

def lane_hits(band_hits_boolvec, W, lanes):
    xs = np.where(band_hits_boolvec)[0]
    if xs.size == 0: return []
    hit_lanes = set()
    for x in xs:
        lane = int(np.clip(x * lanes // W, 0, lanes-1))
        hit_lanes.add(lane)
    return sorted(list(hit_lanes))

# ---------- Main loop with modes ----------
def main():
    if not CALIB_PATH.exists():
        print("calibration.json not found. Run the setup tool first.")
        return

    calib = json.loads(CALIB_PATH.read_text())
    bot = TwoCarsBot(calib)
    cam = Camera(width=320, height=180)

    win = "TwoCarsBot - Runtime"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    # GPIO hardware (graceful if not available)
    led = LED(LED_PIN) if _GPIO_AVAILABLE else None
    if led is not None: led.off()
    button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05) if _GPIO_AVAILABLE else None
    prev_button = False  # edge detect

    def update_led(now):
        if led is None: return
        if mode == MODE_HUMAN:
            led.off()
        elif mode == MODE_ARMING:
            # blink ~4 Hz
            led.on() if int(now * 4) % 2 == 0 else led.off()
        else:  # MODE_BOT
            led.on()

    # Modes
    MODE_HUMAN, MODE_ARMING, MODE_BOT = 0, 1, 2
    mode = MODE_HUMAN
    arming_until = 0.0
    grace_until  = 0.0

    # Game-over detection
    last_pass_time = time.time()
    pass_timeout_s = 7.0
    arming_delay_s = 5.0

    # Ignore-bottom band (to remove cars from detection)
    ignore_enabled = True
    ignore_band_above_car = 0.05   # fraction of height above car line to ignore

    # Scan ROI margin below decision line (fraction of H)
    scan_margin_frac = 0.08        # tune with '-' and '='

    kernel = np.ones((3,3), np.uint8)
    t_last = time.time()

    debug_overlay = True
    sanity_view = False
    last_trip_g = last_trip_r = 0.0
    show_ignore_shading = False   # visual shading in ignored region

    # Flash indicators for test taps
    flash_L_until = 0.0
    flash_R_until = 0.0
    def flash(side, now):
        nonlocal flash_L_until, flash_R_until
        if side == "L": flash_L_until = now + 0.20
        else:           flash_R_until = now + 0.20

    # kill (SIGTERM) and a closed terminal (SIGHUP) end the program like Ctrl+C: the finally below
    # then returns the servos to REST. Installed here, in the main thread, before the main loop.
    def _stop_on_signal(signum, frame):
        raise KeyboardInterrupt
    for _name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, _name):         # SIGHUP does not exist on Windows
            signal.signal(getattr(signal, _name), _stop_on_signal)

    try:
        while True:
            raw = cam.read()
            rect = bot.rect_frame(raw)

            # --- Physical button: same behavior as 'B' key ---
            if button is not None:
                pressed = button.is_pressed  # True while held (to GND)
                if pressed and not prev_button:
                    if mode == MODE_BOT:
                        mode = MODE_HUMAN
                    else:
                        mode = MODE_ARMING
                        arming_until = time.time() + arming_delay_s
                    print(f"[button] mode -> { {0:'HUMAN',1:'ARMING',2:'BOT'}[mode] }")
                prev_button = pressed

            # --- Compute decision line & scan ROI ---
            band_half = 2
            y_dec = int(bot.H * bot.decision_y_frac)
            # ensure ROI covers at least the tripwire band, and extends a margin below the line
            y_scan_max = int(
                min(
                    bot.H,
                    max(y_dec + band_half + 1, int(bot.H * (bot.decision_y_frac + scan_margin_frac)))
                )
            )
            roi = rect[:y_scan_max, :]

            # Masks only in ROI, then placed into full-size arrays
            mg_roi = hsv_mask(roi, bot.good_ranges)
            mb_roi = hsv_mask(roi, bot.bad_ranges)
            mg = np.zeros((bot.H, bot.W), dtype=np.uint8); mg[:y_scan_max, :] = mg_roi
            mb = np.zeros((bot.H, bot.W), dtype=np.uint8); mb[:y_scan_max, :] = mb_roi

            # Compute cutoff (for overlay + optional masking) and remove bottom band
            cutoff_frac = max(bot.decision_y_frac + 0.02, bot.car_y_frac - ignore_band_above_car)
            cutoff_row = int(bot.H * cutoff_frac)
            if ignore_enabled:
                mg[cutoff_row:, :] = 0
                mb[cutoff_row:, :] = 0

            # Clean up masks
            mg = cv2.morphologyEx(mg, cv2.MORPH_OPEN, kernel, iterations=1)
            mb = cv2.morphologyEx(mb, cv2.MORPH_OPEN, kernel, iterations=1)

            # Objects
            good = find_objects(mg, min_area=40)
            bad  = find_objects(mb, min_area=40)

            # Tripwire & game-over timer
            now = time.time()
            y0 = max(0, y_dec - band_half); y1 = min(bot.H, y_dec + band_half + 1)
            mg_band = (mg[y0:y1, :].sum(axis=0) > 0)
            mb_band = (mb[y0:y1, :].sum(axis=0) > 0)
            hit_g = int(mg_band.any()); hit_r = int(mb_band.any())
            if hit_g and now - last_trip_g > 0.15:
                print(f"[trip] GREEN @ t={now:.2f} lane(s): {lane_hits(mg_band, bot.W, bot.lanes)}")
                last_trip_g = now; last_pass_time = now
            if hit_r and now - last_trip_r > 0.15:
                print(f"[trip] RED   @ t={now:.2f} lane(s): {lane_hits(mb_band, bot.W, bot.lanes)}")
                last_trip_r = now; last_pass_time = now

            # Plan (for overlay); execute only in BOT after grace
            plan = bot.plan(good, bad, now)

            if mode == MODE_ARMING:
                if now >= arming_until:
                    bot.tap_side("R")          # logical RIGHT; servo swap handled inside
                    bot.sync_to_game_start()
                    grace_until = now + 1.5
                    last_pass_time = now
                    mode = MODE_BOT
            elif mode == MODE_BOT:
                if now - last_pass_time >= pass_timeout_s:
                    bot.tap_side("R")
                    bot.sync_to_game_start()
                    grace_until = now + 1.5
                    last_pass_time = now
                if now >= grace_until:
                    bot.execute_plan(plan, now)

            # ---- View / overlay ----
            if sanity_view:
                combined = cv2.bitwise_or(mg, mb)
                vis = cv2.bitwise_and(rect, rect, mask=combined)
            else:
                vis = rect.copy()

            if debug_overlay:
                # lane grid & labels
                th = max(2, bot.W // 240)
                for i in range(1, bot.lanes):
                    x = int(i * bot.W / bot.lanes)
                    cv2.line(vis, (x+1, 0), (x+1, bot.H), (0,0,0), th+2, cv2.LINE_AA)
                    cv2.line(vis, (x,   0), (x,   bot.H), (255,255,255), th,   cv2.LINE_AA)
                for i in range(bot.lanes):
                    x0 = int(i * bot.W / bot.lanes); x1 = int((i+1) * bot.W / bot.lanes)
                    cx = (x0 + x1)//2
                    cv2.putText(vis, str(i), (cx-4, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2, cv2.LINE_AA)

                # decision line
                cv2.line(vis, (0, y_dec), (bot.W, y_dec), (255,255,255), 2, cv2.LINE_AA)
                # scan limit line
                cv2.line(vis, (0, max(0, y_scan_max-1)), (bot.W, max(0, y_scan_max-1)), (200,180,0), 1, cv2.LINE_AA)

                # nearest objects per lane (tiny tags)
                nearestG = nearest_per_lane(good, bot.W, bot.lanes)
                nearestB = nearest_per_lane(bad,  bot.W, bot.lanes)
                for lane in range(bot.lanes):
                    if nearestG[lane] is not None:
                        cx, cy, _ = nearestG[lane]
                        cy = min(cy, y_dec-2)
                        cv2.putText(vis, "G", (cx-6, cy-6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2, cv2.LINE_AA)
                    if nearestB[lane] is not None:
                        cx, cy, _ = nearestB[lane]
                        cy = min(cy, y_dec-2)
                        cv2.putText(vis, "R", (cx-6, cy-6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,0,255), 2, cv2.LINE_AA)

                # tripwire markers
                xs_g = np.where(mg_band)[0]; xs_r = np.where(mb_band)[0]
                for x in xs_g: cv2.line(vis, (x, y_dec-8), (x, y_dec+8), (0,255,0), 2, cv2.LINE_AA)
                for x in xs_r: cv2.line(vis, (x, y_dec-8), (x, y_dec+8), (0,0,255), 2, cv2.LINE_AA)

                # car line + optional ignored-region shading
                y_car = int(bot.H * bot.car_y_frac)
                cv2.line(vis, (0, y_car), (bot.W, y_car), (160,160,160), 1, cv2.LINE_AA)
                if ignore_enabled and show_ignore_shading:
                    shaded = vis.copy()
                    cv2.rectangle(shaded, (0, cutoff_row), (bot.W, bot.H), (0,0,0), -1)
                    vis = cv2.addWeighted(shaded, 0.35, vis, 0.65, 0)
                if ignore_enabled:
                    cv2.line(vis, (0, cutoff_row), (bot.W, cutoff_row), (100,100,100), 1, cv2.LINE_AA)

                # expected car markers at car line
                cxL = lane_center_x(bot.left_lane, bot.W, bot.lanes)
                cxR = lane_center_x(bot.right_lane, bot.W, bot.lanes)
                cv2.circle(vis, (cxL, y_car), 8, (0,255,255), 2, cv2.LINE_AA)
                cv2.putText(vis, f"L@{bot.left_lane}", (cxL-24, y_car-12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 2, cv2.LINE_AA)
                cv2.circle(vis, (cxR, y_car), 8, (0,165,255), 2, cv2.LINE_AA)
                cv2.putText(vis, f"R@{bot.right_lane}", (cxR-24, y_car-12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 2, cv2.LINE_AA)

                # decision readout (plan)
                line1 = f"L: cur={plan['L']['cur']} other={plan['L']['other']} | act={'SWITCH' if plan['L']['will_press'] else 'HOLD'} ({plan['L']['reason']}) | cd={plan['L']['cd_remain']:.2f}"
                line2 = f"R: cur={plan['R']['cur']} other={plan['R']['other']} | act={'SWITCH' if plan['R']['will_press'] else 'HOLD'} ({plan['R']['reason']}) | cd={plan['R']['cd_remain']:.2f}"
                cv2.putText(vis, line1, (10, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,255,255), 2, cv2.LINE_AA)
                cv2.putText(vis, line2, (10, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,255,255), 2, cv2.LINE_AA)

                # flash on test taps (HUMAN mode tool)
                if now < flash_L_until:
                    overlay = vis.copy()
                    cv2.rectangle(overlay, (0, int(bot.H*0.75)), (bot.W//2, bot.H), (0,255,255), -1)
                    vis = cv2.addWeighted(overlay, 0.35, vis, 0.65, 0)
                if now < flash_R_until:
                    overlay = vis.copy()
                    cv2.rectangle(overlay, (bot.W//2, int(bot.H*0.75)), (bot.W, bot.H), (0,165,255), -1)
                    vis = cv2.addWeighted(overlay, 0.35, vis, 0.65, 0)

            # HUD
            dt = now - t_last; t_last = now
            fps = (1.0/dt) if dt>1e-6 else 0.0
            mode_txt = {0:"HUMAN",1:"ARMING",2:"BOT"}[mode]
            serv_txt = "SWAP" if bot.servo_swap else "NORM"
            info = f"{bot.W}x{bot.H}  {fps:4.1f} fps  mode:{mode_txt}  SERVOS:{serv_txt}"
            info += f"  scan≤{y_scan_max/bot.H:.2f}"
            if mode == MODE_ARMING: info += f"  starting in {max(0.0, arming_until-now):.1f}s"
            if sanity_view: info += "  [SANITY]"
            if ignore_enabled: info += f"  [ignore≥{cutoff_frac:.2f}]"
            cv2.putText(vis, info, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2, cv2.LINE_AA)

            update_led(now)
            cv2.imshow(win, vis)
            k = cv2.waitKey(1) & 0xFF

            # ---- Keys ----
            if k in (ord('q'), ord('Q'), 27): break
            if k in (ord('f'), ord('F')): cam.refocus()
            if k in (ord('d'), ord('D')): debug_overlay = not debug_overlay
            if k in (ord('c'), ord('C')): sanity_view = not sanity_view
            # Decision line (UNCAPPED 0.0–1.0)
            if k == ord('['): bot.decision_y_frac = max(0.0, bot.decision_y_frac - 0.01)
            if k == ord(']'): bot.decision_y_frac = min(1.0, bot.decision_y_frac + 0.01)
            # Scan margin tuning
            if k == ord('-'):
                scan_margin_frac = max(0.00, scan_margin_frac - 0.01)
                print(f"[scan] margin below decision: {scan_margin_frac:.2f}")
            if k == ord('='):
                scan_margin_frac = min(0.30, scan_margin_frac + 0.01)
                print(f"[scan] margin below decision: {scan_margin_frac:.2f}")
            if k == ord('r'): bot.sync_to_game_start()
            if k in (ord('l'), ord('L')):
                try:
                    new_calib = json.loads(CALIB_PATH.read_text())
                    bot.load_from_calib(new_calib)
                    print("[calib] Reloaded calibration.json")
                except Exception as e:
                    print(f"[calib] Reload failed: {e}")
            if k in (ord('b'), ord('B')):
                if mode == MODE_BOT: mode = MODE_HUMAN
                else:
                    mode = MODE_ARMING
                    arming_until = time.time() + arming_delay_s
            if k in (ord('i'), ord('I')):  # toggle ignore band (detection only)
                ignore_enabled = not ignore_enabled
            if k == ord(','):
                ignore_band_above_car = max(0.01, ignore_band_above_car - 0.01); print(f"[ignore] band above car: {ignore_band_above_car:.2f}")
            if k == ord('.'):
                ignore_band_above_car = min(0.12, ignore_band_above_car + 0.01); print(f"[ignore] band above car: {ignore_band_above_car:.2f}")
            if k in (ord('o'), ord('O')):  # toggle visual shading
                show_ignore_shading = not show_ignore_shading
            if k in (ord('s'), ord('S')):  # SWAP servo mapping
                bot.servo_swap = not bot.servo_swap
                print(f"[servos] mapping = {'SWAPPED (L->right, R->left)' if bot.servo_swap else 'NORMAL (L->left, R->right)'}")
            if k == ord('1'):              # test logical LEFT side tap
                bot.tap_side("L"); flash("L", now)
            if k == ord('2'):              # test logical RIGHT side tap
                bot.tap_side("R"); flash("R", now)

    finally:
        try: close()          # servos to REST first, so nothing below can skip it
        except Exception: pass
        cam.release()
        cv2.destroyAllWindows()
        # GPIO cleanup
        if led is not None:
            try: led.off(); led.close()
            except Exception: pass
        if button is not None:
            try: button.close()
            except Exception: pass

if __name__ == "__main__":
    main()