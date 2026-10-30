#!/usr/bin/env python3
"""LiveKit webhook receiver: post a Discord message when a room starts, delete it when the room finishes.

Listens on 127.0.0.1:8099 (LiveKit runs on the same host network and posts here).
Config from /opt/livekit-bot/.env: DISCORD_TOKEN, DISCORD_CHANNEL_ID (this bot),
LIVEKIT_API_KEY, LIVEKIT_API_SECRET (to verify the webhook signature), MEET_URL.
State (room name -> message id) in /opt/livekit-bot/state.json.
"""
import base64, hashlib, hmac, json, os, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENV = os.environ
TOKEN = ENV["DISCORD_TOKEN"]
CHANNEL = ENV["DISCORD_CHANNEL_ID"]
KEY = ENV["LIVEKIT_API_KEY"]
SECRET = ENV["LIVEKIT_API_SECRET"].encode()
MEET_URL = ENV["MEET_URL"].rstrip("/")
API = ENV.get("DISCORD_API", "https://discord.com/api/v10")  # overridable for tests
STATE = ENV.get("STATE_FILE", "/opt/livekit-bot/state.json")
PORT = int(ENV.get("PORT", "8099"))
LOCK = threading.Lock()


def log(msg):
    print(msg, flush=True)


def b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(auth, body):
    """Check the LiveKit webhook JWT (HS256, issuer = api key, sha256 claim = base64 sha256 of body)."""
    try:
        h, p, s = auth.split(".")
        want = hmac.new(SECRET, f"{h}.{p}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(want, b64url_decode(s)):
            return False
        claims = json.loads(b64url_decode(p))
        if claims.get("iss") != KEY:
            return False
        if claims.get("exp") and claims["exp"] < time.time():
            return False
        digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
        return hmac.compare_digest(claims.get("sha256", ""), digest)
    except Exception:
        return False


def discord(method, path, payload=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bot {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/Rufusnu/LiveKit-Meet-Discord-Bot, 1.0)",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = r.read()
        return json.loads(data) if data else {}


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(state):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def room_started(name):
    with LOCK:
        state = load()
        if name in state:
            return
        link = f"{MEET_URL}/rooms/{urllib.parse.quote(name, safe='')}"
        msg = discord("POST", f"/channels/{CHANNEL}/messages", {
            "content": f"\U0001F3A5 Room **{name}** is open: {link}\n(needs ZeroTier; the message disappears when the room closes)",
            "allowed_mentions": {"parse": []},
        })
        state[name] = msg["id"]
        save(state)
        log(f"posted message {msg['id']} for room {name}")


def room_finished(name):
    with LOCK:
        state = load()
        mid = state.pop(name, None)
        if mid is None:
            return
        try:
            discord("DELETE", f"/channels/{CHANNEL}/messages/{mid}")
            log(f"deleted message {mid} for room {name}")
        except urllib.error.HTTPError as e:
            if e.code != 404:  # 404 = already deleted by someone
                state[name] = mid
                save(state)
                raise
        save(state)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        auth = self.headers.get("Authorization", "")
        if not verify(auth, body):
            self.send_response(401); self.end_headers()
            log("rejected webhook: bad signature")
            return
        try:
            ev = json.loads(body)
            name = ev.get("room", {}).get("name", "")
            if ev.get("event") == "room_started" and name:
                room_started(name)
            elif ev.get("event") == "room_finished" and name:
                room_finished(name)
            self.send_response(200)
        except Exception as e:  # LiveKit retries on non-2xx
            log(f"error handling {body[:80]!r}: {e!r}")
            self.send_response(500)
        self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    log(f"listening on 127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
