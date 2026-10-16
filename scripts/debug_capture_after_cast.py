"""Capture PrintWindow de la fenêtre WoW ~8s après le prochain CAST du bot.

Usage: python scripts/debug_capture_after_cast.py [delay_after_cast]
Sauvegarde dans captures/debug-aftercast-<ts>.png puis exit.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "reports", "fishing-bot-log.jsonl")
OUT = os.path.join(ROOT, "captures")
os.makedirs(OUT, exist_ok=True)

DELAY = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002


def find_wow():
    return user32.FindWindowW(None, "World of Warcraft")


def capture(hwnd, path):
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

    class BMPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    bi = BMPINFOHEADER()
    bi.biSize = ctypes.sizeof(BMPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    from PIL import Image
    img = Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    img.save(path)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return bool(ok)


def last_cast_ts():
    try:
        size = os.path.getsize(LOG)
        with open(LOG, "rb") as f:
            f.seek(max(0, size - 3000))
            data = f.read().decode("utf-8", "replace")
        for line in reversed(data.splitlines()):
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("event") == "CAST":
                return e.get("ts")
    except Exception:
        pass
    return None


def main():
    hwnd = find_wow()
    if not hwnd:
        print("WoW window not found")
        return 1
    print("hwnd=%s delay=%.1f" % (hwnd, DELAY))
    baseline = last_cast_ts()
    print("baseline last CAST: %s" % baseline)
    deadline = time.time() + 90
    while time.time() < deadline:
        cur = last_cast_ts()
        if cur and cur != baseline:
            print("new CAST at %s -> waiting %.1fs" % (cur, DELAY))
            time.sleep(DELAY)
            break
        time.sleep(0.5)
    else:
        print("no new CAST within 90s - capturing anyway")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUT, "debug-aftercast-%s.png" % ts)
    ok = capture(hwnd, path)
    print("captured=%s path=%s" % (ok, path))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
