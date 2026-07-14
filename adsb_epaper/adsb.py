"""Fetch and summarise readsb/tar1090 JSON (aircraft.json, stats.json, receiver.json)."""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.request
from dataclasses import dataclass, field

from .config import Config

log = logging.getLogger(__name__)

NM_PER_KM = 0.539957


@dataclass
class Aircraft:
    hex: str
    callsign: str | None
    alt_baro: int | None      # feet, None for ground/unknown
    gs: float | None          # ground speed, knots
    range_nm: float | None    # distance from receiver


@dataclass
class Snapshot:
    ok: bool = False
    ts: float = 0.0            # wall clock of the poll
    data_age: float = 0.0      # seconds between json "now" and poll time
    total: int = 0             # aircraft tracked
    with_pos: int = 0          # aircraft with a position fix
    msg_rate: float | None = None
    signal_db: float | None = None   # mean signal level, dBFS
    farthest: Aircraft | None = None
    fastest: Aircraft | None = None
    highest: Aircraft | None = None
    aircraft: list[Aircraft] = field(default_factory=list)


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_km * math.asin(math.sqrt(a)) * NM_PER_KM


class AdsbSource:
    """Polls readsb JSON output.  Auto-detects which base URL is live."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base: str | None = None
        self.rx_lat = cfg.lat
        self.rx_lon = cfg.lon
        self._last_msg_counter: tuple[float, int] | None = None

    def _get_json(self, url: str) -> dict:
        with urllib.request.urlopen(url, timeout=self.cfg.http_timeout) as resp:
            return json.load(resp)

    def _detect_base(self) -> str | None:
        for base in self.cfg.base_urls:
            try:
                self._get_json(f"{base}/aircraft.json")
                log.info("using data source %s", base)
                return base
            except Exception:
                continue
        return None

    def _load_receiver_location(self) -> None:
        if self.rx_lat is not None or self.base is None:
            return
        try:
            rx = self._get_json(f"{self.base}/receiver.json")
            if "lat" in rx and "lon" in rx:
                self.rx_lat, self.rx_lon = float(rx["lat"]), float(rx["lon"])
                log.info("receiver location %.3f, %.3f", self.rx_lat, self.rx_lon)
        except Exception:
            log.debug("receiver.json unavailable; range stats disabled unless lat/lon configured")

    def _stats_extras(self) -> tuple[float | None, float | None]:
        """(msg_rate, signal_db) from stats.json when available."""
        try:
            st = self._get_json(f"{self.base}/stats.json")
            one = st.get("last1min", {})
            rate = None
            if "messages" in one:
                span = max(1, one.get("end", 60) - one.get("start", 0))
                rate = one["messages"] / span
            sig = one.get("local", {}).get("signal")
            return rate, sig
        except Exception:
            return None, None

    def poll(self) -> Snapshot:
        now = time.time()
        if self.base is None:
            self.base = self._detect_base()
            self._load_receiver_location()
        if self.base is None:
            return Snapshot(ok=False, ts=now)

        try:
            data = self._get_json(f"{self.base}/aircraft.json")
        except Exception as exc:
            log.warning("aircraft.json fetch failed: %s", exc)
            self.base = None  # re-detect next poll
            return Snapshot(ok=False, ts=now)

        snap = Snapshot(ok=True, ts=now)
        snap.data_age = max(0.0, now - float(data.get("now", now)))

        for a in data.get("aircraft", []):
            if a.get("seen", 999) > 60:
                continue
            alt = a.get("alt_baro")
            alt = alt if isinstance(alt, (int, float)) else None
            rng = None
            if self.rx_lat is not None and "lat" in a and a.get("seen_pos", 999) <= 60:
                rng = haversine_nm(self.rx_lat, self.rx_lon, a["lat"], a["lon"])
            ac = Aircraft(
                hex=a.get("hex", "?"),
                callsign=(a.get("flight") or "").strip() or None,
                alt_baro=alt,
                gs=a.get("gs"),
                range_nm=rng,
            )
            snap.aircraft.append(ac)
            if rng is not None:
                snap.with_pos += 1
            elif "lat" in a and a.get("seen_pos", 999) <= 60:
                snap.with_pos += 1

        snap.total = len(snap.aircraft)

        positioned = [a for a in snap.aircraft if a.range_nm is not None]
        if positioned:
            snap.farthest = max(positioned, key=lambda a: a.range_nm)
        flying = [a for a in snap.aircraft if a.alt_baro is not None]
        if flying:
            snap.highest = max(flying, key=lambda a: a.alt_baro)
        movers = [a for a in snap.aircraft if a.gs is not None]
        if movers:
            snap.fastest = max(movers, key=lambda a: a.gs)

        snap.msg_rate, snap.signal_db = self._stats_extras()
        if snap.msg_rate is None and "messages" in data:
            # fall back to the delta of readsb's lifetime message counter
            cur = (now, int(data["messages"]))
            if self._last_msg_counter:
                t0, c0 = self._last_msg_counter
                if now > t0 and cur[1] >= c0:
                    snap.msg_rate = (cur[1] - c0) / (now - t0)
            self._last_msg_counter = cur

        return snap
