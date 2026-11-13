#!/usr/bin/env python3
"""A stand-in plugNmeet server for the container end-to-end test.

The real plugNmeet needs LiveKit, NATS, Redis and MariaDB, which is far too
much for a free GitHub Actions runner. This speaks the parts of the API that
LearnHouse uses and checks every request exactly like plugNmeet: the API key
header and the hex HMAC-SHA256 of the raw body. It also sends webhooks signed
the way plugNmeet signs them (HS256 JWT with the body's SHA-256), so the
attendance path is tested against the real LearnHouse image.

    fake_plugnmeet.py <port> <api key> <api secret> <webhook url>

Test hooks (not part of plugNmeet):
    GET  /__state                       rooms and issued tokens, as JSON
    POST /__webhook {event, room_id, identity}   send one signed webhook
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, API_KEY, SECRET, WEBHOOK_URL = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
ROOMS: dict[str, dict] = {}
TOKENS: list[dict] = []
REJECTED: list[str] = []


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def webhook_jwt(body: bytes) -> str:
    header = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    claims = {
        "iss": API_KEY,
        "nbf": now,
        "exp": now + 300,
        "sha256": base64.b64encode(hashlib.sha256(body).digest()).decode(),
    }
    payload = b64url(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode()
    return f"{header}.{payload}.{b64url(hmac.new(SECRET.encode(), signing_input, hashlib.sha256).digest())}"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: D102 - quieter logs
        sys.stderr.write("fake-plugnmeet: " + (fmt % args) + "\n")

    def do_GET(self):  # noqa: N802
        if self.path == "/__state":
            return self._send(200, {"rooms": ROOMS, "tokens": TOKENS, "rejected": REJECTED})
        return self._send(200, {"status": True, "msg": "fake plugNmeet"})

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/__webhook":
            spec = json.loads(body)
            event = {"event": spec["event"], "room": {"room_id": spec["room_id"]}, "id": str(time.time_ns()),
                     "created_at": str(int(time.time()))}
            if spec.get("identity"):
                event["participant"] = {"identity": spec["identity"], "name": "x"}
            raw = json.dumps(event).encode()
            token = spec.get("token") or webhook_jwt(raw)
            req = urllib.request.Request(WEBHOOK_URL, data=raw, method="POST", headers={
                "Content-Type": "application/json", "Authorization": token, "Hash-Token": token})
            try:
                # The test's own LearnHouse URL, from the command line.
                with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                    return self._send(200, {"status": resp.status, "body": json.loads(resp.read() or b"{}")})
            except urllib.error.HTTPError as exc:
                return self._send(200, {"status": exc.code})

        expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        if self.headers.get("API-KEY") != API_KEY or not hmac.compare_digest(self.headers.get("HASH-SIGNATURE", ""), expected):
            REJECTED.append(self.path)
            return self._send(401, {"status": False, "msg": "invalid signature"})
        payload = json.loads(body)
        room_id = payload.get("room_id")
        if self.path == "/auth/room/isRoomActive":
            return self._send(200, {"status": True, "msg": "ok", "is_active": room_id in ROOMS})
        if self.path == "/auth/room/create":
            ROOMS[room_id] = payload
            return self._send(200, {"status": True, "msg": "room created"})
        if self.path == "/auth/room/getJoinToken":
            if room_id not in ROOMS:
                return self._send(200, {"status": False, "msg": "room is not active"})
            TOKENS.append(payload)
            return self._send(200, {"status": True, "msg": "ok", "token": f"e2e-token-{len(TOKENS)}"})
        if self.path == "/auth/room/endRoom":
            if ROOMS.pop(room_id, None) is None:
                return self._send(200, {"status": False, "msg": "room is not active"})
            return self._send(200, {"status": True, "msg": "ended"})
        return self._send(404, {"status": False, "msg": "unknown method"})


if __name__ == "__main__":
    # Runs inside its own container on the e2e test network.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # nosec B104
