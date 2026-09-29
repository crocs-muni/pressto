#!/usr/bin/env python3
# calibrate.py
# Adds decision-line overlay + keyboard/click adjustment, saves decision_y_frac.
"""Calibration tool of the cars demo: writes calibration.json for cars_pca.py.

One OpenCV window shows the camera image (Picamera2, or OpenCV camera 0 when
Picamera2 cannot be started) on the left and the rectified game area on the
right. Space freezes a frame; click the four corners of the game area (the
window asks for TL, TR, BR, BL); Enter rectifies. Then G, B and E, each
followed by a click in the right panel, sample the good (green), bad (red)
and empty colours; Y and a click, or [ and ], set the decision line; S saves
once the corners and all three colours are set.

Usage, on the Raspberry Pi, in the folder where cars_pca.py will run:

    python3 calibrate.py

Needs a display: an OpenCV window with keyboard and mouse. It writes
calibration.json into the current folder. Q or Esc quits. README.md next to
this file lists every key.
"""

import json, time
from datetime import datetime
from pathlib import Path
import cv2, numpy as np

# ---------------- Camera (tiny res + AF) ----------------
class Camera:
    def __init__(self, width=320, height=180):
        self.picam2 = None
        self.cap = None
        self.w, self.h = width, height
        self.fps, self.has_autofocus = None, False
        self.libcamera = None
        self._start_picam2() or self._start_opencv()
        print(f"[cam] {self.backend()} {self.w}x{self.h} @{self.fps or 'unknown'}fps")

    def backend(self): return "Picamera2" if self.picam2 is not None else "OpenCV"

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
                    self.picam2.configure(cfg); self.picam2.start(); time.sleep(0.2); self.fps = f; break
                except Exception:
                    try: self.picam2.stop()
                    except Exception: pass
                    self.picam2 = Picamera2()
            try:
                self.picam2.set_controls({"AfMode": self.libcamera.controls.AfModeEnum.Continuous})
                self.has_autofocus = True; print("[cam] Picamera2 AF: Continuous")
            except Exception: pass
            return True
        except Exception as e:
            print(f"[cam] Picamera2 not available ({e})"); self.picam2 = None; return False

    def _start_opencv(self):
        self.cap = cv2.VideoCapture(0)
        if not self.cap.isOpened(): raise RuntimeError("No camera")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.w);  self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.h)
        for f in (120, 90, 60, 30):
            if self.cap.set(cv2.CAP_PROP_FPS, f): self.fps = f; break
        try:
            if hasattr(cv2, "CAP_PROP_AUTOFOCUS"):
                self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1); self.has_autofocus = True; print("[cam] UVC AF: requested")
        except Exception: pass

    def read_raw(self):
        if self.picam2 is not None:
            rgb = self.picam2.capture_array()
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        ok, frame = self.cap.read()
        if not ok: raise RuntimeError("Capture failed")
        return frame

    def refocus(self):
        if self.picam2 is not None and self.has_autofocus:
            try: self.picam2.set_controls({"AfTrigger": self.libcamera.controls.AfTrigger.Start}); print("[cam] AF trigger")
            except Exception: pass
        elif self.cap is not None and self.has_autofocus:
            try: self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0); time.sleep(0.05); self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1); print("[cam] AF toggle")
            except Exception: pass

    def reconfigure(self, width, height):
        self.w, self.h = width, height
        if self.picam2 is not None:
            from picamera2 import Picamera2
            try: self.picam2.stop()
            except Exception: pass
            self.picam2 = Picamera2(); self._start_picam2()
        else:
            self.cap.release(); self._start_opencv()
        print(f"[cam] Reconfigured {self.backend()} {self.w}x{self.h} @{self.fps or 'unknown'}fps")

    def release(self):
        if self.picam2 is not None:
            try: self.picam2.stop()
            except Exception: pass
        if self.cap is not None: self.cap.release()

# ---------------- Helpers ----------------
def overlay_text(img, lines, y0=24):
    y = y0
    for ln in lines:
        cv2.putText(img, ln, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2, cv2.LINE_AA); y += 22

def draw_points(img, pts, color=(0,255,255)):
    for (x,y) in pts: cv2.circle(img, (int(x),int(y)), 5, color, 2)

def median_patch_hsv(bgr, cx, cy, r=6):
    h,w = bgr.shape[:2]; x0,x1 = max(0,cx-r), min(w,cx+r+1); y0,y1 = max(0,cy-r), min(h,cy+r+1)
    hsv = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV); med = np.median(hsv.reshape(-1,3), axis=0)
    return med.astype(np.uint8)

def hsv_ranges_around(center_hsv, dH=12, dS=70, dV=70):
    h,s,v = map(int, center_hsv); loS,hiS = max(0,s-dS),min(255,s+dS); loV,hiV = max(0,v-dV),min(255,v+dV)
    loH,hiH = h-dH, h+dH
    if loH < 0:
        return [([0,loS,loV],[hiH%180,hiS,hiV]), ([180+loH,loS,loV],[179,hiS,hiV])]
    if hiH > 179:
        return [([0,loS,loV],[hiH-180,hiS,hiV]), ([loH,loS,loV],[179,hiS,hiV])]
    return [([loH,loS,loV],[hiH,hiS,hiV])]

def warp_perspective(img, corners_src, dst_size):
    W,H = dst_size; dst = np.float32([[0,0],[W-1,0],[W-1,H-1],[0,H-1]])
    M = cv2.getPerspectiveTransform(np.float32(corners_src), dst)
    return M, cv2.warpPerspective(img, M, (W,H))

def order_quad(pts):
    pts = np.array(pts, dtype=np.float32)
    s = pts.sum(axis=1); d = np.diff(pts, axis=1).reshape(-1)
    ordered = np.zeros((4,2), np.float32)
    ordered[0] = pts[np.argmin(s)]   # TL
    ordered[2] = pts[np.argmax(s)]   # BR
    ordered[1] = pts[np.argmin(d)]   # TR
    ordered[3] = pts[np.argmax(d)]   # BL
    return ordered

# ---------------- Main ----------------
def main():
    save_path = Path("calibration.json")
    cam = Camera(width=320, height=180)

    # Preprocess toggles
    flip_h = False; flip_v = False; rotate_k = 0; swap_rb = False

    # State
    win = "TwoCarsBot - Setup"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    corners, frozen, warped, warpM = [], None, None, None
    hsv_good = hsv_bad = hsv_empty = None
    dst_size = (360, 640)  # rectified portrait size (W,H)
    rect_roi = None        # (x0, y0, w, h) for right panel
    mode = ["idle"]        # 'idle' | 'good' | 'bad' | 'empty' | 'dec'

    # Decision line (fraction of height)
    decision_y_frac = 0.80

    def apply_preproc(img):
        out = img
        if swap_rb: out = out[..., ::-1]          # swap R<->B
        if rotate_k: out = np.ascontiguousarray(np.rot90(out, k=rotate_k))
        if flip_h:   out = cv2.flip(out, 1)
        if flip_v:   out = cv2.flip(out, 0)
        return out

    def on_mouse(event, x, y, flags, param):
        nonlocal hsv_good, hsv_bad, hsv_empty, corners, decision_y_frac
        if event != cv2.EVENT_LBUTTONDOWN: return

        # Corner picking (left panel)
        if frozen is not None and len(corners) < 4:
            corners.append((x,y))
            return

        # Sampling / decision line (right panel)
        if warped is not None and rect_roi is not None and mode[0] in ("good","bad","empty","dec"):
            x0,y0,wR,hR = rect_roi
            if not (x0 <= x < x0+wR and y0 <= y < y0+hR):
                return
            rx = int((x - x0) * warped.shape[1] / wR)
            ry = int((y - y0) * warped.shape[0] / hR)
            rx = np.clip(rx, 0, warped.shape[1]-1)
            ry = np.clip(ry, 0, warped.shape[0]-1)

            if mode[0] == 'dec':
                decision_y_frac = np.clip(ry / warped.shape[0], 0.50, 0.95)
                print(f"[set] decision_y_frac = {decision_y_frac:.3f}")
                return

            hsv = median_patch_hsv(warped, rx, ry, r=6)
            if mode[0] == 'good':  hsv_good = hsv; print(f"[sample] GOOD  HSV {hsv.tolist()} at ({rx},{ry})")
            if mode[0] == 'bad':   hsv_bad  = hsv; print(f"[sample] BAD   HSV {hsv.tolist()} at ({rx},{ry})")
            if mode[0] == 'empty': hsv_empty= hsv; print(f"[sample] EMPTY HSV {hsv.tolist()} at ({rx},{ry})")

    cv2.setMouseCallback(win, on_mouse)

    print("[keys] SPACE freeze; ENTER accept corners; U undo;")
    print("[keys] G/B/E sample Good/Bad/Empty; Y click to set decision line; [ ] nudge; S save calibration;")
    print("[keys] 1/2/3 res 320x180 / 480x270 / 640x360; F refocus;")
    print("[keys] H flip, V flip, T rotate 90°, X swap R↔B; Q quit.")
    try:
        while True:
            frame_raw = cam.read_raw()
            frame = apply_preproc(frame_raw)

            # Left panel
            left = frame.copy()
            h, w = left.shape[:2]
            if frozen is None:
                overlay_text(left, [
                    "LIVE: SPACE to freeze (TL,TR,BR,BL)",
                    f"Transforms: H[{flip_h}] V[{flip_v}] Rot[{rotate_k*90}°] SwapRB[{swap_rb}]",
                    "H/V/T rotate | X swapRB | 1/2/3 res | F refocus | Q quit"
                ])
            else:
                overlay_text(left, ["FROZEN: Click TL,TR,BR,BL. U undo, ENTER accept"])
                draw_points(left, corners)
                if len(corners) >= 2: cv2.line(left, corners[0], corners[1], (0,255,255), 2)
                if len(corners) >= 3: cv2.line(left, corners[1], corners[2], (0,255,255), 2)
                if len(corners) == 4:
                    cv2.line(left, corners[2], corners[3], (0,255,255), 2)
                    cv2.line(left, corners[3], corners[0], (0,255,255), 2)

            # Right panel: rectified with overlays
            if warped is not None:
                right = cv2.resize(warped, (w, h), interpolation=cv2.INTER_LINEAR)
                # draw lane lines + decision line
                th = max(2, w // 240)
                for i in range(1, 4):
                    x = int(i * right.shape[1] / 4)
                    cv2.line(right, (x+1, 0), (x+1, right.shape[0]), (0,0,0), th+2, cv2.LINE_AA)
                    cv2.line(right, (x,   0), (x,   right.shape[0]), (255,255,255), th, cv2.LINE_AA)
                y_dec = int(decision_y_frac * right.shape[0])
                cv2.line(right, (0, y_dec), (right.shape[1], y_dec), (255,255,255), 2, cv2.LINE_AA)

                overlay_text(right, [
                    f"RECTIFIED: mode={mode[0]} | Y click to set decision line",
                    f"decision_y_frac={decision_y_frac:.3f}  |  [ / ] to nudge 1%"
                ], y0=22)
            else:
                right = np.zeros_like(left)

            pad = np.zeros((h, 12, 3), dtype=np.uint8)
            disp = np.hstack([left, pad, right])
            rect_roi = (w + 12, 0, w, h) if warped is not None else None

            cv2.imshow(win, disp)
            k = cv2.waitKey(1) & 0xFF

            if k in (ord('q'), ord('Q'), 27): break
            if k in (ord('f'), ord('F')): cam.refocus()
            if k == ord('1'): cam.reconfigure(320, 180)
            if k == ord('2'): cam.reconfigure(480, 270)
            if k == ord('3'): cam.reconfigure(640, 360)
            if k in (ord('h'), ord('H')): flip_h = not flip_h
            if k in (ord('v'), ord('V')): flip_v = not flip_v
            if k in (ord('t'), ord('T')): rotate_k = (rotate_k + 1) % 4
            if k in (ord('x'), ord('X')): swap_rb = not swap_rb

            if k == ord(' '):  # freeze for corners
                frozen = frame.copy(); corners = []; warped = None; mode[0] = "idle"
                print("[step] Frozen. Click TL,TR,BR,BL. ENTER to accept.")

            if k in (ord('u'), ord('U')) and frozen is not None and corners:
                corners.pop()

            if k in (13, 10) and frozen is not None:
                if len(corners) == 4:
                    ordered = order_quad(corners)
                    warpM, warped = warp_perspective(frozen, ordered, dst_size=(360,640))
                    frozen = None
                    print("[step] Warp OK. Press G/B/E to sample colors; Y to set decision line; S to save.")
                else:
                    print("[warn] Need 4 corners.")

            if k in (ord('r'), ord('R')):  # clear samples (keep warp)
                hsv_good = hsv_bad = hsv_empty = None; mode[0] = "idle"; print("[step] Cleared color samples.")

            if k in (ord('g'), ord('G')) and warped is not None: mode[0] = "good";  print("[mode] GOOD: click green (RIGHT panel)")
            if k in (ord('b'), ord('B')) and warped is not None: mode[0] = "bad";   print("[mode] BAD:  click red   (RIGHT panel)")
            if k in (ord('e'), ord('E')) and warped is not None: mode[0] = "empty"; print("[mode] EMPTY: click background (RIGHT panel)")
            if k in (ord('y'), ord('Y')) and warped is not None: mode[0] = "dec";   print("[mode] DECISION LINE: click position (RIGHT panel)")

            # Nudge decision line by 1%
            if k == ord('[') and warped is not None:
                decision_y_frac = max(0.50, decision_y_frac - 0.01)
            if k == ord(']') and warped is not None:
                decision_y_frac = min(0.95, decision_y_frac + 0.01)

            # Save calibration
            if k in (ord('s'), ord('S')) and warpM is not None and hsv_good is not None and hsv_bad is not None and hsv_empty is not None:
                calib = {
                    "saved_at": datetime.now().isoformat(),
                    "rectified_size": {"w": 360, "h": 640},
                    "preprocess": { "flip_h": flip_h, "flip_v": flip_v, "rotate_k": rotate_k, "swap_rb": swap_rb },
                    "warp_matrix": warpM.tolist(),
                    "good_hsv_center": hsv_good.tolist(),
                    "bad_hsv_center":  hsv_bad.tolist(),
                    "empty_hsv_center":hsv_empty.tolist(),
                    "good_hsv_ranges": hsv_ranges_around(hsv_good.tolist()),
                    "bad_hsv_ranges":  hsv_ranges_around(hsv_bad.tolist()),
                    "lanes": 4,
                    "decision_y_frac": float(decision_y_frac),
                    "car_y_frac": 0.88,
                }
                with open(save_path, "w") as f: json.dump(calib, f, indent=2)
                print(f"[save] Wrote {save_path.resolve()}")

    finally:
        cam.release(); cv2.destroyAllWindows()

if __name__ == "__main__":
    main()