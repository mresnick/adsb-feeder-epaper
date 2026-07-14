"""Display backends: the real Waveshare 3.52\" (B) panel, or a PNG file for dev."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PIL import Image, ImageOps

from .config import Config

log = logging.getLogger(__name__)

PAPER = (245, 243, 237)
INK = (20, 20, 20)
RED_INK = (188, 32, 28)


def composite(black: Image.Image, red: Image.Image) -> Image.Image:
    """Flatten the two ink layers into an RGB rendition of the panel.

    Layers may be 1-bit (panel frames) or grayscale (anti-aliased web
    renders); ink coverage is 255 minus the layer value either way.
    """
    out = Image.new("RGB", black.size, PAPER)
    red_ink = Image.new("RGB", black.size, RED_INK)
    out = Image.composite(red_ink, out, ImageOps.invert(red.convert("L")))
    black_ink = Image.new("RGB", black.size, INK)
    return Image.composite(black_ink, out, ImageOps.invert(black.convert("L")))


class PngDisplay:
    """Writes the composited frame to a PNG file."""

    def __init__(self, cfg: Config):
        self.path = Path(cfg.png_path)
        self.rotate = cfg.rotate_180

    def show(self, black: Image.Image, red: Image.Image) -> None:
        if self.rotate:
            black, red = black.rotate(180), red.rotate(180)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        composite(black, red).save(self.path)
        log.info("preview written to %s", self.path)

    def close(self) -> None:
        pass


class EpdDisplay:
    """Waveshare 3.52\" (B). Full refresh only; deep-sleeps between updates."""

    def __init__(self, cfg: Config):
        lib = Path(__file__).resolve().parent.parent / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        from waveshare_epd import epd3in52b  # noqa: PLC0415
        self.epd = epd3in52b.EPD()
        self.rotate = cfg.rotate_180

    def show(self, black: Image.Image, red: Image.Image) -> None:
        if self.rotate:
            black, red = black.rotate(180), red.rotate(180)
        self.epd.init()
        self.epd.display(self.epd.getbuffer(black), self.epd.getbuffer(red))
        self.epd.sleep()

    def clear(self) -> None:
        self.epd.init()
        self.epd.Clear()
        self.epd.sleep()

    def close(self) -> None:
        try:
            self.epd.sleep()
        except Exception:
            pass


def make_display(cfg: Config):
    if cfg.driver == "png":
        return PngDisplay(cfg)
    if cfg.driver == "epd":
        return EpdDisplay(cfg)
    # auto: use the panel if its GPIO/SPI stack is importable
    try:
        return EpdDisplay(cfg)
    except Exception as exc:
        log.warning("EPD unavailable (%s); falling back to PNG output", exc)
        return PngDisplay(cfg)
