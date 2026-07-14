"""Dev preview: render the dashboard with sample data to out/preview*.png.

Run on any machine with Pillow:  python preview.py
"""

from __future__ import annotations

import math
import random
import time

from adsb_epaper.display import composite
from adsb_epaper.render import View, render
from PIL import Image


def fake_history(hours: float = 6.5) -> list[tuple[float, int]]:
    random.seed(7)
    now = time.time()
    pts = []
    t = now - hours * 3600
    while t < now:
        hour = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60
        base = 28 + 22 * math.sin((hour - 5) / 24 * 2 * math.pi * 1.6)
        pts.append((t, max(0, int(base + random.gauss(0, 4)))))
        t += 30
    return pts


def healthy() -> View:
    return View(
        time_str="18:42", ip="192.168.1.50",
        aircraft_now=47, peak_today=63,
        msg_rate=1243.0,
        range_now_nm=187.2, range_now_call="UAL123",
        seen_today=1842,
        range_today_nm=218.6, range_today_call="BAW284",
        history=fake_history(),
        cpu_pct=23.0, temp_c=61.2, ram_pct=41.0, disk_pct=38.0,
        uptime_s=12.4 * 86400, signal_db=-18.4,
    )


def alerting() -> View:
    v = healthy()
    v.temp_c, v.temp_alert = 81.0, True
    v.disk_pct, v.disk_alert = 93.0, True
    v.undervolt = True
    return v


def no_data() -> View:
    v = healthy()
    v.ok = False
    v.stale_minutes = 12
    v.msg_rate = None
    v.range_now_nm = None
    v.range_now_call = None
    v.aircraft_now = 0
    v.signal_db = None
    return v


if __name__ == "__main__":
    for name, view in [("healthy", healthy()), ("alerts", alerting()), ("nodata", no_data())]:
        black, red = render(view)
        img = composite(black, red)
        path = f"out/preview_{name}.png"
        img.resize((720, 480), Image.NEAREST).save(path)
        print("wrote", path)

    # the anti-aliased render the web UI serves
    black, red = render(healthy(), scale=2)
    composite(black, red).save("out/preview_smooth.png")
    print("wrote out/preview_smooth.png")
