#!/usr/bin/env python3
"""
test_camera.py

Minimal camera debug script extracted from calibrate.py's Camera backend logic,
with extra diagnostics to understand why cv2.VideoCapture.read() returns False.

The cars demo's camera check: run it before calibrate.py. It opens no window:
it prints what it finds, reads --frames frames and saves the last one to
--save (default cam_debug_frame.jpg); with --probe it only tries the OpenCV
device indices 0 to 9. python3 test_camera.py --help lists every option.

Usage examples:
  python3 test_camera.py --probe
  python3 test_camera.py --index 0 --width 320 --height 180 --frames 30 --save frame.jpg
  python3 test_camera.py --force-opencv --index 0
  python3 test_camera.py --force-picam2
"""

import argparse
import glob
import os
import sys
import time
import subprocess

import cv2


def run_cmd(cmd):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return out.strip()
    except Exception as e:
        return f"(failed: {e})"


def print_env():
    print("[env] python:", sys.version.split()[0])
    print("[env] opencv:", getattr(cv2, "__version__", "unknown"))
    vids = sorted(glob.glob("/dev/video*"))
    print("[env] /dev/video*:", " ".join(vids) if vids else "(none)")
    if shutil_which("v4l2-ctl"):
        print("[env] v4l2-ctl --list-devices:\n" + run_cmd(["v4l2-ctl", "--list-devices"]))
    else:
        print("[env] v4l2-ctl: not found (sudo apt install -y v4l-utils)")


def shutil_which(name):
    # tiny replacement for shutil.which (avoids import; works on old environments too)
    for p in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(p, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def cap_props(cap):
    # capture a few helpful props (not all backends support all)
    def get(prop):
        try:
            return cap.get(prop)
        except Exception:
            return None

    fourcc = int(get(cv2.CAP_PROP_FOURCC) or 0)
    fourcc_str = "".join([chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)])
    return {
        "backend": get(cv2.CAP_PROP_BACKEND),
        "w": get(cv2.CAP_PROP_FRAME_WIDTH),
        "h": get(cv2.CAP_PROP_FRAME_HEIGHT),
        "fps": get(cv2.CAP_PROP_FPS),
        "fourcc": fourcc_str,
    }


def open_opencv(index, width, height, fps, mjpg=False, backend_hint=True):
    # Prefer CAP_V4L2 when available (often more predictable on Linux)
    if backend_hint and hasattr(cv2, "CAP_V4L2"):
        cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    else:
        cap = cv2.VideoCapture(index)

    if not cap.isOpened():
        return cap, False

    # Try to request a stable format first
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))

    if mjpg:
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass

    if fps:
        try:
            cap.set(cv2.CAP_PROP_FPS, float(fps))
        except Exception:
            pass

    # autofocus request if supported
    try:
        if hasattr(cv2, "CAP_PROP_AUTOFOCUS"):
            cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
    except Exception:
        pass

    return cap, True


class Camera:
    """
    Adapted from the Camera class of calibrate.py; adds a configurable OpenCV index, a forced backend (--force-opencv / --force-picam2), an MJPG request (--mjpg) and read retries.
    """
    def __init__(self, width=320, height=180, index=0, force_backend="auto", mjpg=False):
        self.picam2 = None
        self.cap = None
        self.w, self.h = width, height
        self.index = index
        self.fps, self.has_autofocus = None, False
        self.libcamera = None
        self.mjpg = mjpg
        self.force_backend = force_backend

        started = False
        if force_backend in ("auto", "picam2"):
            started = self._start_picam2()
        if (not started) and force_backend in ("auto", "opencv"):
            self._start_opencv()

        print(f"[cam] {self.backend()} {self.w}x{self.h} @{self.fps or 'unknown'}fps")

        if self.cap is not None:
            print("[cam] OpenCV props:", cap_props(self.cap))

    def backend(self):
        return "Picamera2" if self.picam2 is not None else "OpenCV"

    def _start_picam2(self):
        if self.force_backend == "opencv":
            return False
        try:
            from picamera2 import Picamera2
            import libcamera
            self.libcamera = libcamera
            self.picam2 = Picamera2()

            # Try a few FPS targets, like in calibrate.py
            for f in (120, 90, 60, 30):
                try:
                    cfg = self.picam2.create_video_configuration(
                        main={"size": (self.w, self.h), "format": "RGB888"},
                        controls={"FrameDurationLimits": (int(1e6 / f), int(1e6 / f))}
                    )
                    self.picam2.configure(cfg)
                    self.picam2.start()
                    time.sleep(0.2)
                    self.fps = f
                    break
                except Exception:
                    try:
                        self.picam2.stop()
                    except Exception:
                        pass
                    self.picam2 = Picamera2()

            try:
                self.picam2.set_controls({"AfMode": self.libcamera.controls.AfModeEnum.Continuous})
                self.has_autofocus = True
                print("[cam] Picamera2 AF: Continuous")
            except Exception:
                pass

            return True
        except Exception as e:
            print(f"[cam] Picamera2 not available ({e})")
            self.picam2 = None
            return False

    def _start_opencv(self):
        if self.force_backend == "picam2":
            raise RuntimeError("Forced Picamera2 but it failed to start.")
        self.cap, ok = open_opencv(self.index, self.w, self.h, fps=None, mjpg=self.mjpg)
        if not ok:
            raise RuntimeError(f"No camera at index {self.index}")

        # Like calibrate.py, try to request a high FPS first
        for f in (120, 90, 60, 30):
            try:
                if self.cap.set(cv2.CAP_PROP_FPS, f):
                    self.fps = f
                    break
            except Exception:
                pass

        # autofocus request (best-effort)
        try:
            if hasattr(cv2, "CAP_PROP_AUTOFOCUS"):
                self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
                self.has_autofocus = True
                print("[cam] UVC AF: requested")
        except Exception:
            pass

    def read_raw(self, retries=10, retry_delay=0.05):
        if self.picam2 is not None:
            rgb = self.picam2.capture_array()
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        # OpenCV path: retry a few times (some devices return False for initial frames)
        last_ok = None
        for i in range(retries):
            ok, frame = self.cap.read()
            last_ok = ok
            if ok and frame is not None:
                return frame
            time.sleep(retry_delay)

        raise RuntimeError(f"Capture failed (ok={last_ok}) after {retries} retries")

    def release(self):
        if self.picam2 is not None:
            try:
                self.picam2.stop()
            except Exception:
                pass
        if self.cap is not None:
            self.cap.release()


def probe_indices(width, height, mjpg=False, max_index=10):
    print(f"[probe] Trying indices 0..{max_index-1} with {width}x{height}, mjpg={mjpg}")
    for i in range(max_index):
        cap, ok = open_opencv(i, width, height, fps=None, mjpg=mjpg)
        if not ok:
            print(f"[probe] {i}: not opened")
            continue

        props = cap_props(cap)
        # Try a few reads (warmup)
        got = False
        shape = None
        for _ in range(10):
            ret, frame = cap.read()
            if ret and frame is not None:
                got = True
                shape = frame.shape
                break
            time.sleep(0.05)

        print(f"[probe] {i}: opened, read={'OK' if got else 'FAIL'}, shape={shape}, props={props}")
        cap.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--height", type=int, default=180)
    ap.add_argument("--index", type=int, default=0, help="OpenCV device index (only used for OpenCV backend)")
    ap.add_argument("--frames", type=int, default=30, help="How many frames to attempt")
    ap.add_argument("--save", type=str, default="cam_debug_frame.jpg", help="Where to save the last frame (BGR->JPG)")
    ap.add_argument("--probe", action="store_true", help="Probe /dev/video indices 0..9 using OpenCV")
    ap.add_argument("--mjpg", action="store_true", help="Request MJPG FOURCC for OpenCV (USB webcams often like this)")
    ap.add_argument("--force-opencv", action="store_true", help="Force OpenCV backend (skip Picamera2)")
    ap.add_argument("--force-picam2", action="store_true", help="Force Picamera2 backend (error if unavailable)")
    args = ap.parse_args()

    print_env()

    if args.probe:
        probe_indices(args.width, args.height, mjpg=args.mjpg, max_index=10)
        return

    force = "auto"
    if args.force_opencv and args.force_picam2:
        raise SystemExit("Pick only one: --force-opencv OR --force-picam2")
    if args.force_opencv:
        force = "opencv"
    elif args.force_picam2:
        force = "picam2"

    cam = Camera(width=args.width, height=args.height, index=args.index, force_backend=force, mjpg=args.mjpg)

    last = None
    try:
        # Try reading a bunch of frames, like the loop of calibrate.py would do
        for n in range(args.frames):
            t0 = time.time()
            frame = cam.read_raw()
            dt = (time.time() - t0) * 1000
            last = frame
            if n % max(1, args.frames // 10) == 0:
                print(f"[read] {n:03d}/{args.frames} ok shape={frame.shape} dt={dt:.1f}ms mean={frame.mean():.1f}")
            time.sleep(0.01)

        # Save the final frame so you can see what OpenCV/Picamera2 actually returns
        if last is not None:
            ok = cv2.imwrite(args.save, last)
            print(f"[save] {args.save} -> {'OK' if ok else 'FAIL'}")
    finally:
        cam.release()


if __name__ == "__main__":
    main()