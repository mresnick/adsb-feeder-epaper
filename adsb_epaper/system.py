"""Raspberry Pi system metrics from /proc, /sys and vcgencmd (no external deps)."""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

# vcgencmd get_throttled bits that describe the CURRENT state
UNDERVOLT_NOW = 1 << 0
THROTTLED_NOW = 1 << 2
TEMP_LIMIT_NOW = 1 << 3


@dataclass
class SystemMetrics:
    cpu_pct: float | None = None
    temp_c: float | None = None
    ram_pct: float | None = None
    disk_pct: float | None = None
    uptime_s: float | None = None
    ip: str | None = None
    undervolt: bool = False
    throttled: bool = False


class SystemCollector:
    def __init__(self) -> None:
        self._last_cpu: tuple[float, float] | None = None  # (busy, total)

    # -- individual readers, each fails soft -----------------------------------

    def _cpu_pct(self) -> float | None:
        try:
            with open("/proc/stat") as fh:
                parts = fh.readline().split()[1:]
            vals = [float(v) for v in parts]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0.0)  # idle + iowait
            total = sum(vals)
            busy = total - idle
            if self._last_cpu is not None:
                b0, t0 = self._last_cpu
                dt = total - t0
                if dt > 0:
                    pct = 100.0 * (busy - b0) / dt
                    self._last_cpu = (busy, total)
                    return max(0.0, min(100.0, pct))
            self._last_cpu = (busy, total)
            return None
        except OSError:
            return None

    @staticmethod
    def _temp_c() -> float | None:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as fh:
                return int(fh.read().strip()) / 1000.0
        except (OSError, ValueError):
            return None

    @staticmethod
    def _ram_pct() -> float | None:
        try:
            info: dict[str, int] = {}
            with open("/proc/meminfo") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    info[key] = int(rest.split()[0])
            total = info["MemTotal"]
            avail = info.get("MemAvailable", info.get("MemFree", 0))
            return 100.0 * (total - avail) / total
        except (OSError, KeyError, ValueError, ZeroDivisionError):
            return None

    @staticmethod
    def _disk_pct() -> float | None:
        try:
            du = shutil.disk_usage("/")
            return 100.0 * du.used / du.total
        except OSError:
            return None

    @staticmethod
    def _uptime_s() -> float | None:
        try:
            with open("/proc/uptime") as fh:
                return float(fh.read().split()[0])
        except (OSError, ValueError):
            return None

    @staticmethod
    def _ip() -> str | None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return None

    @staticmethod
    def _throttle_bits() -> int:
        try:
            out = subprocess.run(
                ["vcgencmd", "get_throttled"],
                capture_output=True, text=True, timeout=3,
            ).stdout
            return int(out.split("=")[1], 16)
        except (OSError, subprocess.SubprocessError, IndexError, ValueError):
            return 0

    # ---------------------------------------------------------------------------

    def sample(self) -> SystemMetrics:
        m = SystemMetrics(
            cpu_pct=self._cpu_pct(),
            temp_c=self._temp_c(),
            ram_pct=self._ram_pct(),
            disk_pct=self._disk_pct(),
            uptime_s=self._uptime_s(),
            ip=self._ip(),
        )
        bits = self._throttle_bits()
        m.undervolt = bool(bits & UNDERVOLT_NOW)
        m.throttled = bool(bits & (THROTTLED_NOW | TEMP_LIMIT_NOW))
        return m
