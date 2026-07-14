"""Daily aggregates and sparkline history, persisted across restarts."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .adsb import Snapshot

log = logging.getLogger(__name__)

HISTORY_KEEP_S = 24 * 3600
PERSIST_EVERY_S = 300


@dataclass
class DailyStats:
    day: str = ""                      # ISO date the stats belong to
    hexes: set[str] = field(default_factory=set)
    peak_concurrent: int = 0
    max_range_nm: float = 0.0
    max_range_call: str = ""

    @property
    def seen_today(self) -> int:
        return len(self.hexes)


class State:
    def __init__(self, path: Path):
        self.path = path
        self.daily = DailyStats(day=date.today().isoformat())
        self.history: list[tuple[float, int]] = []  # (ts, aircraft w/ pos)
        self._last_persist = 0.0
        self._load()

    # -- persistence -------------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.path) as fh:
                raw = json.load(fh)
            if raw.get("day") == date.today().isoformat():
                self.daily = DailyStats(
                    day=raw["day"],
                    hexes=set(raw.get("hexes", [])),
                    peak_concurrent=raw.get("peak_concurrent", 0),
                    max_range_nm=raw.get("max_range_nm", 0.0),
                    max_range_call=raw.get("max_range_call", ""),
                )
            cutoff = time.time() - HISTORY_KEEP_S
            self.history = [
                (t, n) for t, n in raw.get("history", []) if t >= cutoff
            ]
            log.info("restored state: %d aircraft today, %d history points",
                     self.daily.seen_today, len(self.history))
        except (OSError, ValueError, KeyError):
            pass

    def persist(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_persist < PERSIST_EVERY_S:
            return
        self._last_persist = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w") as fh:
                json.dump({
                    "day": self.daily.day,
                    "hexes": sorted(self.daily.hexes),
                    "peak_concurrent": self.daily.peak_concurrent,
                    "max_range_nm": self.daily.max_range_nm,
                    "max_range_call": self.daily.max_range_call,
                    "history": self.history,
                }, fh)
            tmp.replace(self.path)
        except OSError as exc:
            log.warning("could not persist state: %s", exc)

    # ------------------------------------------------------------------------------

    def update(self, snap: Snapshot) -> None:
        today = date.today().isoformat()
        if today != self.daily.day:
            log.info("midnight rollover; %d aircraft seen on %s",
                     self.daily.seen_today, self.daily.day)
            self.daily = DailyStats(day=today)

        if snap.ok:
            self.daily.hexes.update(a.hex for a in snap.aircraft)
            self.daily.peak_concurrent = max(self.daily.peak_concurrent, snap.with_pos)
            if snap.farthest and snap.farthest.range_nm and \
                    snap.farthest.range_nm > self.daily.max_range_nm:
                self.daily.max_range_nm = snap.farthest.range_nm
                self.daily.max_range_call = snap.farthest.callsign or snap.farthest.hex

            self.history.append((snap.ts, snap.with_pos))
            cutoff = snap.ts - HISTORY_KEEP_S
            while self.history and self.history[0][0] < cutoff:
                self.history.pop(0)

        self.persist()
