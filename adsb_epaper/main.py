"""Service entry point: poll data every poll_seconds, redraw the panel when due."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from .adsb import AdsbSource, Snapshot
from .config import Config, load_config
from .display import make_display
from .render import View, render
from .state import State
from .system import SystemCollector, SystemMetrics

log = logging.getLogger("adsb_epaper")


def build_view(cfg: Config, snap: Snapshot, m: SystemMetrics, state: State) -> View:
    v = View(
        time_str=time.strftime("%H:%M"),
        ip=m.ip,
        ok=snap.ok and snap.data_age <= cfg.stale_data_seconds,
        aircraft_now=snap.with_pos,
        peak_today=state.daily.peak_concurrent,
        msg_rate=snap.msg_rate,
        seen_today=state.daily.seen_today,
        history=state.history,
        history_hours=cfg.history_hours,
        cpu_pct=m.cpu_pct,
        temp_c=m.temp_c,
        ram_pct=m.ram_pct,
        disk_pct=m.disk_pct,
        uptime_s=m.uptime_s,
        signal_db=snap.signal_db,
        undervolt=m.undervolt,
        throttled=m.throttled,
        units=cfg.units,
    )
    if not v.ok:
        v.stale_minutes = int(max(snap.data_age, time.time() - snap.ts) // 60)
    if snap.farthest:
        v.range_now_nm = snap.farthest.range_nm
        v.range_now_call = snap.farthest.callsign or snap.farthest.hex
    if state.daily.max_range_nm > 0:
        v.range_today_nm = state.daily.max_range_nm
        v.range_today_call = state.daily.max_range_call
    v.temp_alert = m.temp_c is not None and m.temp_c >= cfg.temp_warn_c
    v.ram_alert = m.ram_pct is not None and m.ram_pct >= cfg.ram_warn_pct
    v.disk_alert = m.disk_pct is not None and m.disk_pct >= cfg.disk_warn_pct
    return v


def alert_key(v: View) -> tuple:
    return (v.ok, v.temp_alert, v.ram_alert, v.disk_alert, v.undervolt, v.throttled)


def run(cfg: Config) -> None:
    source = AdsbSource(cfg)
    collector = SystemCollector()
    state = State(cfg.state_path)
    display = make_display(cfg)

    web = None
    if cfg.webui_enabled:
        from .webui import WebUI
        try:
            web = WebUI(cfg.webui_port, cfg.webui_bind)
            web.start()
        except OSError as exc:
            log.warning("web ui disabled: %s", exc)
            web = None

    stopping = False

    def _stop(signum, frame):  # noqa: ARG001
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    last_draw = 0.0
    last_alerts: tuple | None = None
    collector.sample()  # prime the CPU counter

    while not stopping:
        loop_start = time.time()
        snap = source.poll()
        state.update(snap)
        metrics = collector.sample()
        view = build_view(cfg, snap, metrics, state)

        due = loop_start - last_draw >= cfg.refresh_seconds
        alerts = alert_key(view)
        alert_flip = (
            last_alerts is not None
            and alerts != last_alerts
            and loop_start - last_draw >= cfg.min_refresh_seconds
        )
        if last_draw == 0.0 or due or alert_flip:
            log.info(
                "refresh: %d aircraft, %d today, alerts=%s",
                view.aircraft_now, view.seen_today, alerts,
            )
            black, red = render(view)
            try:
                display.show(black, red)
                last_draw = time.time()
                last_alerts = alerts
                if web:
                    web.update(black, red, cfg.refresh_seconds)
            except Exception:
                log.exception("panel refresh failed")

        remaining = cfg.poll_seconds - (time.time() - loop_start)
        while remaining > 0 and not stopping:
            time.sleep(min(1.0, remaining))
            remaining -= 1.0

    state.persist(force=True)
    display.close()
    log.info("stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="ADS-B feeder e-paper display")
    parser.add_argument("-c", "--config", type=Path, help="path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--once", action="store_true",
                        help="poll, draw once, then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    cfg = load_config(args.config)
    if args.once:
        cfg.refresh_seconds = 0
        source = AdsbSource(cfg)
        collector = SystemCollector()
        collector.sample()
        time.sleep(1)
        state = State(cfg.state_path)
        snap = source.poll()
        state.update(snap)
        view = build_view(cfg, snap, collector.sample(), state)
        black, red = render(view)
        make_display(cfg).show(black, red)
        state.persist(force=True)
        return
    run(cfg)


if __name__ == "__main__":
    main()
