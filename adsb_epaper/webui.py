"""Tiny built-in web UI mirroring exactly what the e-paper panel shows.

Serves the last frame that was pushed to the panel:
    /            small HTML page (auto-refreshing)
    /frame.png   the composited frame, 2x nearest-neighbour
    /status.json rendered_at + refresh cadence
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

from .display import composite

log = logging.getLogger(__name__)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ADS-B feeder display</title>
<style>
  :root { color-scheme: light dark; }
  body {
    margin: 0; min-height: 100vh; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 14px;
    font: 14px/1.5 system-ui, sans-serif;
    background: #ddd8ce; color: #333;
  }
  @media (prefers-color-scheme: dark) {
    body { background: #1c1c1e; color: #bbb; }
  }
  img {
    width: min(92vw, 720px); image-rendering: pixelated;
    border: 10px solid #17171a; border-radius: 6px;
    box-shadow: 0 6px 24px rgb(0 0 0 / 35%);
  }
  #meta { opacity: .75; }
</style>
</head>
<body>
  <img id="frame" src="/frame.png" alt="current e-paper frame" width="720" height="480">
  <div id="meta">&nbsp;</div>
<script>
async function tick() {
  try {
    const s = await (await fetch('/status.json', {cache: 'no-store'})).json();
    const age = Math.round((Date.now()/1000 - s.rendered_at) / 60);
    document.getElementById('meta').textContent =
      `panel refreshed ${age} min ago · redraws every ${Math.round(s.refresh_seconds/60)} min`;
    if (s.rendered_at !== window._last) {
      window._last = s.rendered_at;
      document.getElementById('frame').src = '/frame.png?t=' + s.rendered_at;
    }
  } catch (e) { document.getElementById('meta').textContent = 'service unreachable'; }
}
tick(); setInterval(tick, 15000);
</script>
</body>
</html>
"""


class WebUI:
    def __init__(self, port: int, bind: str = "0.0.0.0"):
        self.port = port
        self.bind = bind
        self._png: bytes | None = None
        self._rendered_at = 0.0
        self._refresh_seconds = 300
        self._lock = threading.Lock()

    def update(self, black: Image.Image, red: Image.Image,
               refresh_seconds: int) -> None:
        img = composite(black, red)
        img = img.resize((img.width * 2, img.height * 2), Image.NEAREST)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        with self._lock:
            self._png = buf.getvalue()
            self._rendered_at = time.time()
            self._refresh_seconds = refresh_seconds

    def start(self) -> None:
        ui = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # journald is noisy enough
                log.debug("http: " + fmt, *args)

            def _send(self, code: int, ctype: str, body: bytes):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                with ui._lock:
                    png, at, every = ui._png, ui._rendered_at, ui._refresh_seconds
                if path == "/":
                    self._send(200, "text/html; charset=utf-8", PAGE.encode())
                elif path == "/frame.png":
                    if png is None:
                        self._send(503, "text/plain", b"no frame rendered yet")
                    else:
                        self._send(200, "image/png", png)
                elif path == "/status.json":
                    self._send(200, "application/json", json.dumps({
                        "rendered_at": at,
                        "refresh_seconds": every,
                    }).encode())
                else:
                    self._send(404, "text/plain", b"not found")

        server = ThreadingHTTPServer((self.bind, self.port), Handler)
        thread = threading.Thread(target=server.serve_forever,
                                  name="webui", daemon=True)
        thread.start()
        log.info("web ui listening on http://%s:%d/", self.bind, self.port)
