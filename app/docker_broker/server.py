"""docker_broker.server — the HTTP forwarder that enforces docker_broker.policy.

Run as ``python -m app.docker_broker``. Listens on ``DOCKER_BROKER_LISTEN``
(default ``0.0.0.0:2375``) and forwards every request to
``DOCKER_BROKER_UPSTREAM`` (default ``http://docker-proxy:2375``) UNCHANGED,
EXCEPT ``POST .../containers/create`` whose JSON body must pass
:func:`app.docker_broker.policy.validate_create_body` — otherwise it returns
``403`` and the request is never forwarded.

Holds no docker socket; talks only to the upstream proxy over TCP; runs
unprivileged. A bug here fails CLOSED (the gateway can't reach Docker), never
opening a new escape. Standard request/response only — no attach/exec hijack
(EXEC is off on the proxy); image-pull progress is buffered then returned.
"""
from __future__ import annotations

import json
import logging
import os
import re
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from app.docker_broker.policy import validate_create_body

logger = logging.getLogger("docker_broker")

# Matches /containers/create with or without an API-version prefix (/v1.43/...).
_CREATE_RE = re.compile(r"^(?:/v[0-9][0-9.]*)?/containers/create$")

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
}

# Generous upstream timeout — /containers/{id}/wait long-polls until the
# container exits; the gateway's own client timeout is the real bound.
_UPSTREAM_TIMEOUT = float(os.environ.get("DOCKER_BROKER_UPSTREAM_TIMEOUT", "1800"))


def _upstream() -> tuple[str, int]:
    raw = os.environ.get("DOCKER_BROKER_UPSTREAM", "http://docker-proxy:2375")
    u = urlparse(raw if "://" in raw else "http://" + raw)
    return (u.hostname or "docker-proxy", u.port or 2375)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "docker-broker/1.0"

    def log_message(self, fmt, *args):  # route access logs through logging
        logger.info("%s %s", self.address_string(), fmt % args)

    def _read_body(self) -> bytes:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            chunks: list[bytes] = []
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    continue
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline()  # consume trailing CRLF
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()  # CRLF after each chunk
            return b"".join(chunks)
        cl = self.headers.get("Content-Length")
        return self.rfile.read(int(cl)) if cl else b""

    def _respond(self, code: int, payload: bytes, content_type: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _deny(self, reason: str, code: int = 403):
        self._respond(code, json.dumps({"message": f"docker-broker refused: {reason}"}).encode())

    def _handle(self):
        try:
            body = self._read_body()
        except Exception as exc:  # malformed request framing
            return self._deny(f"could not read request body: {exc}", 400)

        path_only = self.path.split("?", 1)[0]
        if self.command == "POST" and _CREATE_RE.match(path_only):
            try:
                parsed = json.loads(body or b"{}")
            except Exception:
                return self._deny("unparseable create body", 400)
            ok, reason = validate_create_body(parsed)
            if not ok:
                logger.warning("BLOCKED create: %s | image=%r", reason,
                               parsed.get("Image") if isinstance(parsed, dict) else None)
                return self._deny(reason)

        host, port = _upstream()
        try:
            conn = HTTPConnection(host, port, timeout=_UPSTREAM_TIMEOUT)
            fwd_headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP}
            fwd_headers["Content-Length"] = str(len(body))
            conn.request(self.command, self.path, body=body, headers=fwd_headers)
            resp = conn.getresponse()
            data = resp.read()
        except Exception as exc:
            logger.warning("upstream error (%s:%d): %s", host, port, exc)
            return self._deny(f"upstream unreachable: {exc}", 502)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in _HOP_BY_HOP or k.lower() == "content-length":
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # Docker uses GET/POST/DELETE/PUT/HEAD; PATCH for completeness.
    do_GET = do_POST = do_DELETE = do_PUT = do_HEAD = do_PATCH = _handle


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s docker_broker %(levelname)s %(message)s",
    )
    listen = os.environ.get("DOCKER_BROKER_LISTEN", "0.0.0.0:2375")
    host, _, port = listen.rpartition(":")
    srv = ThreadingHTTPServer((host or "0.0.0.0", int(port or 2375)), _Handler)
    up_host, up_port = _upstream()
    from app.docker_broker.policy import image_allowlist
    logger.info("docker-broker listening on %s → upstream http://%s:%d (image allowlist: %s)",
                listen, up_host, up_port, ",".join(image_allowlist()))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        srv.server_close()
    return 0
