"""360x240 two-layer (black + red) dashboard renderer.

Design rules for this panel:
  * red is reserved for alerts -- a healthy feeder shows a pure B/W screen
  * one hero figure (aircraft currently tracked), everything else is quiet
  * no anti-aliasing: text is drawn straight onto 1-bit images
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 360, 240

BLACK, WHITE = 0, 255

_FONT_DIRS = [
    Path(__file__).resolve().parent.parent / "assets" / "fonts",
    Path("/usr/share/fonts/truetype/dejavu"),
]


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for d in _FONT_DIRS:
        p = d / name
        if p.is_file():
            return ImageFont.truetype(str(p), size)
    raise FileNotFoundError(f"font {name} not found in {_FONT_DIRS}")


class Fonts:
    def __init__(self, scale: int = 1) -> None:
        self.header = _font("DejaVuSans-Bold.ttf", 13 * scale)
        self.hero = _font("DejaVuSans-Bold.ttf", 60 * scale)
        self.hero_small = _font("DejaVuSans-Bold.ttf", 40 * scale)
        self.label = _font("DejaVuSans.ttf", 11 * scale)
        self.value = _font("DejaVuSans-Bold.ttf", 24 * scale)
        self.unit = _font("DejaVuSans.ttf", 12 * scale)
        self.small = _font("DejaVuSans.ttf", 10 * scale)
        self.foot_label = _font("DejaVuSans.ttf", 9 * scale)
        self.foot_value = _font("DejaVuSans-Bold.ttf", 15 * scale)
        self.alert = _font("DejaVuSans-Bold.ttf", 22 * scale)


@dataclass
class View:
    """Everything the renderer needs, already formatted-agnostic."""
    time_str: str = "--:--"
    ip: str | None = None

    ok: bool = True                    # data source healthy
    stale_minutes: int = 0

    aircraft_now: int = 0
    peak_today: int = 0
    msg_rate: float | None = None
    range_now_nm: float | None = None
    range_now_call: str | None = None
    seen_today: int = 0
    range_today_nm: float | None = None
    range_today_call: str | None = None

    history: list[tuple[float, int]] = field(default_factory=list)
    history_hours: int = 6

    cpu_pct: float | None = None
    temp_c: float | None = None
    ram_pct: float | None = None
    disk_pct: float | None = None
    uptime_s: float | None = None
    signal_db: float | None = None

    temp_alert: bool = False
    ram_alert: bool = False
    disk_alert: bool = False
    undervolt: bool = False
    throttled: bool = False

    units: str = "nm"


# -- formatting helpers -------------------------------------------------------

def _num(n: float | None, digits: int = 0) -> str:
    if n is None:
        return "--"
    if n >= 10000:
        return f"{n / 1000:.1f}k"
    return f"{n:,.{digits}f}"


def _dist(nm: float | None, units: str) -> tuple[str, str]:
    if nm is None:
        return "--", ""
    factor = {"nm": 1.0, "km": 1.852, "mi": 1.15078}.get(units, 1.0)
    return f"{nm * factor:.0f}", units


def _uptime(s: float | None) -> str:
    if s is None:
        return "--"
    d, rem = divmod(int(s), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d > 0:
        return f"{d}d{h}h"
    if h > 0:
        return f"{h}h{m}m"
    return f"{m}m"


# -- two-layer canvas -----------------------------------------------------------

class Canvas:
    """Two ink layers with logical 360x240 coordinates.

    scale=1 renders 1-bit with FreeType mono hinting (what the panel needs);
    scale>1 renders anti-aliased grayscale at that multiple (for the web UI).
    """

    def __init__(self, scale: int = 1) -> None:
        self.s = scale
        mode = "1" if scale == 1 else "L"
        size = (WIDTH * scale, HEIGHT * scale)
        self.black = Image.new(mode, size, WHITE)
        self.red = Image.new(mode, size, WHITE)
        self.dk = ImageDraw.Draw(self.black)
        self.dr = ImageDraw.Draw(self.red)

    def _xy(self, xy):
        return tuple(v * self.s for v in xy)

    def text(self, xy, s, font, layer="k", fill=BLACK, anchor=None):
        (self.dk if layer == "k" else self.dr).text(
            self._xy(xy), s, font=font, fill=fill, anchor=anchor)

    def text_w(self, s, font) -> int:
        """Logical width of a string (font is already scale-sized)."""
        return int(self.dk.textlength(s, font=font) / self.s)

    def rect(self, box, layer="k", fill=None, outline=None):
        d = self.dk if layer == "k" else self.dr
        d.rectangle(self._xy(box), fill=fill, outline=outline, width=self.s)

    def line(self, pts, layer="k", fill=BLACK):
        d = self.dk if layer == "k" else self.dr
        d.line(self._xy(pts), fill=fill, width=self.s)

    def red_badge(self, box, s, font):
        """Solid red box with knocked-out white text; black layer cleared under it."""
        self.rect(box, layer="k", fill=WHITE)
        self.rect(box, layer="r", fill=BLACK)
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        self.text((cx, cy), s, font, layer="r", fill=WHITE, anchor="mm")

    def meter(self, x, y, w, h, pct, layer="k"):
        self.rect((x, y, x + w, y + h), layer=layer, outline=BLACK)
        if pct is not None and pct > 0:
            fill_w = max(1, int((w - 2) * min(pct, 100.0) / 100.0))
            self.rect((x + 1, y + 1, x + 1 + fill_w, y + h - 1), layer=layer, fill=BLACK)


# -- sections ---------------------------------------------------------------------

HDR_H = 24
SPARK_TOP = 158
FOOT_TOP = 202


def _header(c: Canvas, f: Fonts, v: View) -> None:
    c.rect((0, 0, WIDTH, HDR_H - 1), fill=BLACK)
    title = "✈ ADS-B FEEDER"
    c.text((8, HDR_H // 2), title, f.header, fill=WHITE, anchor="lm")

    # power alerts live in the header, right after the title
    chips = []
    if v.undervolt:
        chips.append("! PWR")
    if v.throttled:
        chips.append("! THROTTLE")
    chip_end = 8 + c.text_w(title, f.header)
    if chips:
        s = "  ".join(chips)
        x0 = chip_end + 18
        w = c.text_w(s, f.header)
        c.red_badge((x0, 2, x0 + w + 12, HDR_H - 3), s, f.header)
        chip_end = x0 + w + 12

    right = v.time_str if v.ip is None else f"{v.ip}   {v.time_str}"
    if 8 + c.text_w(right, f.header) + chip_end > WIDTH:
        right = v.time_str  # not enough room; the IP yields to the alert
    c.text((WIDTH - 8, HDR_H // 2), right, f.header, fill=WHITE, anchor="rm")


def _hero(c: Canvas, f: Fonts, v: View) -> None:
    cx = 58
    if not v.ok:
        c.red_badge((6, 46, 112, 84), "NO DATA", f.header)
        c.text((cx, 102), f"for {v.stale_minutes} min", f.small, anchor="mm")
        return

    n = str(v.aircraft_now)
    font = f.hero if len(n) <= 2 else f.hero_small
    c.text((cx, 74), n, font, anchor="mm")
    c.text((cx, 116), "aircraft now", f.label, anchor="mm")
    c.text((cx, 134), f"peak today {v.peak_today}", f.small, anchor="mm")


def _tiles(c: Canvas, f: Fonts, v: View) -> None:
    c.line((118, HDR_H + 10, 118, SPARK_TOP - 10))

    rate = _num(v.msg_rate)
    rng_now, u1 = _dist(v.range_now_nm, v.units)
    rng_day, u2 = _dist(v.range_today_nm, v.units)

    def tile(x, y, label, value, unit="", sub=""):
        c.text((x, y), label, f.label)
        vw = c.text_w(value, f.value)
        c.text((x, y + 14), value, f.value)
        if unit:
            c.text((x + vw + 4, y + 24), unit, f.unit)
        if sub:
            c.text((x, y + 44), sub, f.small)

    ax, bx = 132, 248
    r1, r2 = HDR_H + 12, HDR_H + 76

    tile(ax, r1, "messages / sec", "--" if not v.ok else rate)
    tile(bx, r1, "range now", "--" if not v.ok else rng_now, u1,
         sub=(v.range_now_call or "") if v.ok else "")
    tile(ax, r2, "aircraft today", _num(v.seen_today))
    tile(bx, r2, "range today", rng_day, u2, sub=v.range_today_call or "")


def _sparkline(c: Canvas, f: Fonts, v: View) -> None:
    x0, x1 = 10, WIDTH - 10
    top, base = SPARK_TOP + 14, FOOT_TOP - 6
    c.text((x0, SPARK_TOP), f"aircraft · last {v.history_hours} h", f.small)

    now = time.time()
    span = v.history_hours * 3600
    pts = [(t, n) for t, n in v.history if t >= now - span]
    if len(pts) < 2:
        c.text(((x0 + x1) // 2, (top + base) // 2), "collecting history…",
               f.small, anchor="mm")
        c.line((x0, base, x1, base))
        return

    step = 4                      # 3px bar + 1px gap
    nbuck = (x1 - x0) // step
    buckets = [0] * nbuck
    for t, n in pts:
        i = min(nbuck - 1, int((t - (now - span)) / span * nbuck))
        buckets[i] = max(buckets[i], n)

    vmax = max(max(buckets), 5)
    peak_i = buckets.index(max(buckets))
    hgt = base - top
    for i, n in enumerate(buckets):
        if n <= 0:
            continue
        h = max(1, int(n / vmax * hgt))
        x = x0 + i * step
        c.rect((x, base - h, x + 2, base), fill=BLACK)

    c.line((x0, base, x1, base))

    # selective direct label: the peak only
    peak_x = min(max(x0 + peak_i * step, x0 + 8), x1 - 12)
    c.text((peak_x, top - 11), str(max(buckets)), f.small, anchor="mm")


def _footer(c: Canvas, f: Fonts, v: View) -> None:
    c.line((0, FOOT_TOP, WIDTH, FOOT_TOP))

    cells = [
        ("cpu", f"{v.cpu_pct:.0f}%" if v.cpu_pct is not None else "--",
         v.cpu_pct, False),
        ("temp", f"{v.temp_c:.0f}°C" if v.temp_c is not None else "--",
         None, v.temp_alert),
        ("ram", f"{v.ram_pct:.0f}%" if v.ram_pct is not None else "--",
         v.ram_pct, v.ram_alert),
        ("disk", f"{v.disk_pct:.0f}%" if v.disk_pct is not None else "--",
         v.disk_pct, v.disk_alert),
        ("uptime", _uptime(v.uptime_s), None, False),
        ("signal", f"{v.signal_db:.0f}dB" if v.signal_db is not None else "--",
         None, False),
    ]

    cw = WIDTH // len(cells)
    for i, (label, value, pct, alert) in enumerate(cells):
        cx = i * cw + cw // 2
        if alert:
            box = (i * cw + 3, FOOT_TOP + 4, (i + 1) * cw - 3, HEIGHT - 4)
            c.rect(box, layer="k", fill=WHITE)
            c.rect(box, layer="r", fill=BLACK)
            c.text((cx, FOOT_TOP + 12), label, f.foot_label, layer="r",
                   fill=WHITE, anchor="mm")
            c.text((cx, FOOT_TOP + 27), f"! {value}", f.foot_value, layer="r",
                   fill=WHITE, anchor="mm")
            continue
        c.text((cx, FOOT_TOP + 11), label, f.foot_label, anchor="mm")
        c.text((cx, FOOT_TOP + 24), value, f.foot_value, anchor="mm")
        if pct is not None:
            c.meter(cx - 20, HEIGHT - 7, 40, 4, pct)


def render(v: View, scale: int = 1) -> tuple[Image.Image, Image.Image]:
    """scale=1: 1-bit panel frame. scale>1: anti-aliased grayscale layers."""
    c = Canvas(scale)
    f = Fonts(scale)
    _header(c, f, v)
    _hero(c, f, v)
    _tiles(c, f, v)
    _sparkline(c, f, v)
    _footer(c, f, v)
    return c.black, c.red
