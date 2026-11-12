"""Tiny local web server (127.0.0.1 only) for the tracker UI. Standard library, no framework."""

from __future__ import annotations

import base64
import binascii
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .service import Tracker

WEB = Path(__file__).parent / "web"
TYPES = {".png": "image/png", ".ico": "image/x-icon", ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}


def make_handler(tracker: Tracker):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Tracker/1.0"

        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        # ------------------------------------------------------------ helpers
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self, limit: int = 1_000_000) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise ValueError("request too large")
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("JSON object expected")
            return data

        # ------------------------------------------------------------ routes
        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            person = q.get("person", [""])[0] or None
            try:
                if url.path in ("/", "/index.html"):
                    return self._static("index.html")
                if url.path.startswith("/static/"):
                    return self._static(url.path[len("/static/"):])
                if url.path == "/api/data":
                    return self._json(200, tracker.view(person))
                if url.path == "/api/prompt":
                    op_id = q.get("id", [""])[0]
                    return self._json(200, {"id": op_id, "prompt": tracker.prompt(op_id, person)})
                if url.path == "/api/refresh":
                    return self._json(200, tracker.refresh_status)
                return self._json(404, {"error": "not found"})
            except KeyError:
                return self._json(404, {"error": "unknown position or person"})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                return self._json(500, {"error": f"{e.__class__.__name__}: {e}"})

        def do_POST(self):
            url = urlparse(self.path)
            try:
                if not self._same_origin():
                    return self._json(403, {"error": "cross-site request refused"})
                if url.path == "/api/people":
                    return self._add_person(self._body(limit=22_000_000))
                body = self._body()
                person = str(body["person"]) if body.get("person") else None
                if url.path == "/api/status":
                    tracker.set_status(str(body["id"]), str(body["status"]), person)
                    return self._json(200, {"ok": True})
                if url.path == "/api/note":
                    tracker.set_note(str(body["id"]), str(body.get("note", "")), person)
                    return self._json(200, {"ok": True})
                if url.path == "/api/weights":
                    if body.get("reset"):
                        tracker.reset_weights(person)
                    else:
                        tracker.set_weights(body.get("weights", {}), person)
                    return self._json(200, tracker.view(person))
                if url.path == "/api/refresh":
                    started = tracker.start_refresh()
                    return self._json(202 if started else 409, tracker.refresh_status)
                return self._json(404, {"error": "not found"})
            except KeyError:
                return self._json(404, {"error": "unknown position or missing field"})
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                return self._json(500, {"error": f"{e.__class__.__name__}: {e}"})

        def _same_origin(self) -> bool:
            """Only this page may change things: a website you visit must not be able to post to localhost."""
            origin = self.headers.get("Origin")
            if not origin:
                return True
            host = urlparse(origin).hostname
            return host in ("127.0.0.1", "localhost", "::1")

        def _add_person(self, body: dict) -> None:
            if body.get("sample"):
                from .config import SAMPLE_CV
                cv, name = SAMPLE_CV.read_bytes(), str(body.get("name") or "Alex (sample)")
            else:
                try:
                    cv = base64.b64decode(str(body.get("cv_base64") or ""), validate=True)
                except (binascii.Error, ValueError):
                    raise ValueError("the CV could not be read")
                name = str(body.get("name") or "")
            p = tracker.add_person(name, cv, looking_for=str(body.get("looking_for") or ""),
                                   available_from=str(body.get("available_from") or ""),
                                   needs_visa_sponsorship=str(body.get("needs_visa_sponsorship") or "unknown"))
            if body.get("refresh", True) and not tracker.s.opportunities_file.exists():
                tracker.start_refresh()
            return self._json(201, {"slug": p.slug, "name": p.name, "refresh": tracker.refresh_status})

        def _static(self, name: str) -> None:
            path = (WEB / name).resolve()
            if WEB.resolve() not in path.parents or not path.is_file():
                return self._json(404, {"error": "not found"})
            self._send(200, path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"))

    return Handler


def make_server(tracker: Tracker, port: int = 8787) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(tracker))
