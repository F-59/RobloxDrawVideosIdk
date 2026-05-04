'''
(by Floor_59)
Works in draw space and spray paint, dont know about the others i havent tested

How to use:
    Run the script then go to roblox
    (unequip the paint tool if you have it equipped)
    Watch the magic begin
    Press M to stop the magic
'''

import cv2
import numpy as np
import pydirectinput
import time
import threading
import keyboard
from scipy.spatial import cKDTree
from itertools import groupby
import ctypes
import mss

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

# CONFIGS
VIDEO_PATH  = r"inputpath.mp4"
OUTPUT_PATH = r"outputpath.mp4"
CANVAS_X    = 150 # canvas top left corner coords
CANVAS_Y    = 150
SCALE       = 4
TARGET_FPS  = 6
START_TIME  = 65
START_DELAY = 3
PAINT_KEY   = '3'
ERASE_KEY   = '4'
KILL_KEY    = 'm'
MARGIN      = 300

user32 = ctypes.windll.user32
SCREEN_W, SCREEN_H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
MAX_CANVAS_W = (SCREEN_W - CANVAS_X - MARGIN) // SCALE
MAX_CANVAS_H = (SCREEN_H - CANVAS_Y - MARGIN) // SCALE
stop_flag = threading.Event()

def to_screen(x, y):
    return int(CANVAS_X + x * SCALE), int(CANVAS_Y + y * SCALE)

def get_edges(frame):
    ys, xs = np.where(cv2.Canny(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 20, 200) > 0)
    return set(zip(xs.tolist(), ys.tolist()))

def extract_frames(path, fps, max_w, max_h, start_sec):
    cap = cv2.VideoCapture(path)
    src_fps = cap.get(cv2.CAP_PROP_FPS)
    step = max(1, round(src_fps / fps))
    vid_w, vid_h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) / src_fps if src_fps else 1.0

    start_sec = max(0, min(start_sec, duration - 0.1))
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_sec * src_fps))

    scale = min(max_w / vid_w, max_h / vid_h)
    new_w, new_h = int(vid_w * scale), int(vid_h * scale)

    frames, idx = [], 0
    while (ret := cap.read())[0]:
        if idx % step == 0:
            frames.append(cv2.resize(ret[1], (new_w, new_h), interpolation=cv2.INTER_AREA))
        idx += 1
    cap.release()

    print(f"Extracted {len(frames)} frames ({new_w}x{new_h}) from {duration:.1f}s video")
    return frames, new_w, new_h, duration - start_sec

def tsp_sort(pts):
    if not pts: return []
    pts = np.array(list(pts))
    tree = cKDTree(pts)
    n, order, used, k = len(pts), [0], np.zeros(n, bool), 64
    used[0], left = True, n - 1
    while left:
        for i in tree.query(pts[order[-1]], k=min(k, n))[1]:
            if not used[i]:
                order.append(i); used[i] = True; left -= 1; break
        else:
            k = min(k * 2, n)
    return [tuple(p) for p in pts[order]]

def raster_sort(pts):
    if not pts: return []
    result = []
    for i, (_, row) in enumerate(groupby(sorted(pts, key=lambda p: (p[1], p[0])), key=lambda p: p[1])):
        row = list(row)
        result.extend(row[::-1] if i % 2 else row)
    return result

class Pen:
    def __init__(self):
        self.down = self.cx = self.cy = 0
        self.tool = None

    def switch_to(self, tool):
        if self.tool != tool:
            self._up()
            pydirectinput.press(PAINT_KEY if tool == 'brush' else ERASE_KEY)
            time.sleep(0.01)
            self.tool = tool

    def _up(self):
        if self.down:
            pydirectinput.mouseUp()
            self.down = False
            time.sleep(0.008)

    def _jump(self, tx, ty):
        self._up()
        tx, ty = np.clip([tx, ty], 0, [SCREEN_W - 1, SCREEN_H - 1])
        pydirectinput.moveTo(int(tx), int(ty))
        time.sleep(0.005)
        self.cx, self.cy = tx, ty

    def paint(self, pts, jump_thresh):
        if not pts: return
        self._jump(*pts[0])
        pydirectinput.mouseDown()
        self.down = True

        for tx, ty in pts[1:]:
            if stop_flag.is_set(): return
            tx, ty = np.clip([tx, ty], 0, [SCREEN_W - 1, SCREEN_H - 1])
            if abs(tx - self.cx) + abs(ty - self.cy) > jump_thresh:
                self._up()
                pydirectinput.moveTo(int(tx), int(ty))
                time.sleep(0.005)
                self.cx, self.cy = tx, ty
                pydirectinput.mouseDown()
                self.down = True
            else:
                pydirectinput.moveTo(int(tx), int(ty))
                self.cx, self.cy = tx, ty
        self._up()

def save_video(shots, total_frames, duration, path):
    if not shots: return
    fps = total_frames / duration if duration else TARGET_FPS
    h, w = shots[0].shape[:2]
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
    for img in shots: out.write(img)
    out.release()
    print(f"Saved: {path} ({len(shots)}/{total_frames} frames, {fps:.1f} FPS)")

def run():
    frames, w, h, duration = extract_frames(VIDEO_PATH, TARGET_FPS, MAX_CANVAS_W, MAX_CANVAS_H, START_TIME)
    if not frames: return print("No frames extracted, check the video path")

    region = {"left": CANVAS_X, "top": CANVAS_Y, "width": w * SCALE, "height": h * SCALE}
    print(f"Canvas: {MAX_CANVAS_W}x{MAX_CANVAS_H} units, Starting in {START_DELAY}s\n[{KILL_KEY}] to stop")
    time.sleep(START_DELAY)

    pydirectinput.PAUSE = 0.003
    pen, prev, shots, sct = Pen(), set(), [], mss.mss()
    jump_thresh = SCALE * 4

    try:
        for i, frame in enumerate(frames):
            if stop_flag.is_set(): break
            curr = get_edges(frame)
            erase, paint = prev - curr, curr - prev
            print(f"[{i+1}/{len(frames)}] erase = {len(erase)} paint = {len(paint)}")

            if erase:
                pen.switch_to('eraser')
                pen.paint([to_screen(*p) for p in raster_sort(erase)], jump_thresh)
            if paint and not stop_flag.is_set():
                pen.switch_to('brush')
                pen.paint([to_screen(*p) for p in tsp_sort(paint)], jump_thresh)
            if not stop_flag.is_set():
                shots.append(cv2.cvtColor(np.array(sct.grab(region)), cv2.COLOR_BGRA2BGR))
            prev = curr
    finally:
        pen._up(); sct.close()

    save_video(shots, len(frames), duration, OUTPUT_PATH)

if __name__ == "__main__":
    threading.Thread(target=lambda: (keyboard.wait(KILL_KEY), stop_flag.set()), daemon=True).start()
    run()
