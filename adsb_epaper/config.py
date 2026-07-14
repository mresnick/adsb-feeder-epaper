"""Configuration loading (TOML with sensible defaults for an adsb.im feeder image)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG_PATHS = [
    Path("/etc/adsb-epaper/config.toml"),
    PROJECT_ROOT / "config.toml",
]


@dataclass
class Config:
    # -- data sources ---------------------------------------------------------
    # Candidate base URLs tried in order; the first that serves aircraft.json
    # wins.  Both layouts used by the adsb.im image are covered.
    base_urls: list[str] = field(default_factory=lambda: [
        "http://127.0.0.1/tar1090/data",
        "http://127.0.0.1:8080/data",
    ])
    http_timeout: float = 5.0

    # Receiver location fallback (used for range if receiver.json has no lat/lon)
    lat: float | None = None
    lon: float | None = None

    # -- timing ----------------------------------------------------------------
    poll_seconds: int = 30          # data sampling cadence
    refresh_seconds: int = 300      # normal panel redraw cadence
    min_refresh_seconds: int = 180  # Waveshare floor for 3-colour panels
    stale_data_seconds: int = 90    # no fresh aircraft.json for this long -> NO DATA

    # -- display ---------------------------------------------------------------
    driver: str = "auto"            # auto | epd | png
    rotate_180: bool = False        # enclosure mounted upside down
    png_path: Path = PROJECT_ROOT / "out" / "preview.png"
    units: str = "nm"               # nm | km | mi

    # -- alert thresholds --------------------------------------------------------
    temp_warn_c: float = 75.0
    ram_warn_pct: float = 90.0
    disk_warn_pct: float = 90.0

    # -- web ui --------------------------------------------------------------------
    webui_enabled: bool = True
    webui_port: int = 8099
    webui_bind: str = "0.0.0.0"

    # -- persistence -------------------------------------------------------------
    state_path: Path = Path("/var/lib/adsb-epaper/state.json")

    # -- sparkline ---------------------------------------------------------------
    history_hours: int = 6


def _apply(cfg: Config, data: dict) -> None:
    src = data.get("source", {})
    if "base_urls" in src:
        cfg.base_urls = list(src["base_urls"])
    cfg.http_timeout = float(src.get("http_timeout", cfg.http_timeout))
    if "lat" in src:
        cfg.lat = float(src["lat"])
    if "lon" in src:
        cfg.lon = float(src["lon"])

    timing = data.get("timing", {})
    cfg.poll_seconds = int(timing.get("poll_seconds", cfg.poll_seconds))
    cfg.refresh_seconds = int(timing.get("refresh_seconds", cfg.refresh_seconds))
    cfg.min_refresh_seconds = int(timing.get("min_refresh_seconds", cfg.min_refresh_seconds))
    cfg.stale_data_seconds = int(timing.get("stale_data_seconds", cfg.stale_data_seconds))

    disp = data.get("display", {})
    cfg.driver = disp.get("driver", cfg.driver)
    cfg.rotate_180 = bool(disp.get("rotate_180", cfg.rotate_180))
    if "png_path" in disp:
        cfg.png_path = Path(disp["png_path"])
    cfg.units = disp.get("units", cfg.units)
    cfg.history_hours = int(disp.get("history_hours", cfg.history_hours))

    alerts = data.get("alerts", {})
    cfg.temp_warn_c = float(alerts.get("temp_warn_c", cfg.temp_warn_c))
    cfg.ram_warn_pct = float(alerts.get("ram_warn_pct", cfg.ram_warn_pct))
    cfg.disk_warn_pct = float(alerts.get("disk_warn_pct", cfg.disk_warn_pct))

    web = data.get("webui", {})
    cfg.webui_enabled = bool(web.get("enabled", cfg.webui_enabled))
    cfg.webui_port = int(web.get("port", cfg.webui_port))
    cfg.webui_bind = web.get("bind", cfg.webui_bind)

    state = data.get("state", {})
    if "path" in state:
        cfg.state_path = Path(state["path"])


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    candidates = [path] if path else DEFAULT_CONFIG_PATHS
    for p in candidates:
        if p and p.is_file():
            with open(p, "rb") as fh:
                _apply(cfg, tomllib.load(fh))
            break
    return cfg
