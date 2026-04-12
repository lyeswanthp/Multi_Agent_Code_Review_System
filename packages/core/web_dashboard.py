"""Web dashboard server — serves a live monitoring UI over HTTP with SSE.

Runs in a background thread. Zero external dependencies (stdlib only).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from code_review.events import Event, bus

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "web_static"


class _DashboardHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for the web dashboard."""

    def log_message(self, format, *args):
        # Suppress default stderr logging from http.server
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass  # Client disconnected — normal on Windows

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_file("index.html", "text/html")
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/api/history":
            self._serve_history()
        elif self.path == "/api/config":
            self._serve_config()
        else:
            self.send_error(404)

    def _serve_file(self, filename: str, content_type: str):
        filepath = _STATIC_DIR / filename
        if not filepath.exists():
            self.send_error(404, f"File not found: {filename}")
            return
        content = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_sse(self):
        """Server-Sent Events endpoint — streams events in real-time."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        q: queue.Queue[Event] = queue.Queue()

        def _on_event(event: Event):
            q.put(event)

        bus.subscribe(_on_event)
        try:
            while True:
                try:
                    event = q.get(timeout=1.0)
                    data = event.to_json()
                    self.wfile.write(f"event: {event.kind}\ndata: {data}\n\n".encode())
                    self.wfile.flush()
                except queue.Empty:
                    # Send keepalive
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.unsubscribe(_on_event)

    def _serve_history(self):
        """Return all past events as JSON array."""
        events = [{"kind": e.kind, "data": e.data, "ts": e.ts} for e in bus.history]
        body = json.dumps(events, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_config(self):
        """Return current config as JSON."""
        try:
            from code_review.config import settings
            config = {
                "llm_mode": settings.llm_mode,
                "heavy_model": settings.lmstudio_heavy_model,
                "light_model": settings.lmstudio_light_model,
                "context_size": settings.lmstudio_context_size,
                "base_url": settings.lmstudio_base_url,
                "severity_threshold": settings.severity_threshold.value,
            }
        except Exception:
            config = {"error": "Could not load config"}
        body = json.dumps(config).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class DashboardServer:
    """Manages the web dashboard HTTP server in a background thread."""

    def __init__(self, port: int = 9120):
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """Start the server and return the URL."""
        self._server = HTTPServer(("127.0.0.1", self.port), _DashboardHandler)
        self._server.timeout = 1
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        url = f"http://127.0.0.1:{self.port}"
        logger.info("Dashboard running at %s", url)
        return url

    def _run(self):
        while self._server:
            self._server.handle_request()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
