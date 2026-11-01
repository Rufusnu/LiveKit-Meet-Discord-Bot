#!/usr/bin/env python3
"""LiveKit webhook receiver: post a Discord message with the room link when somebody starts sharing their screen
(OBS or the browser share button), delete that message when they stop (or leave, or the room finishes).

Listens on 127.0.0.1:8099 (LiveKit runs on the same host network and posts here).
Config from /opt/livekit-bot/.env: DISCORD_TOKEN, DISCORD_CHANNEL_ID (this bot),
LIVEKIT_API_KEY, LIVEKIT_API_SECRET (to verify the webhook signature), MEET_URL.
State (room + participant identity -> message id) in /opt/livekit-bot/state.json.
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


def key(room, identity):
    return f"{room}\t{identity}"


def delete_message(mid):
    try:
        discord("DELETE", f"/channels/{CHANNEL}/messages/{mid}")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:  # already deleted by someone
            return True
        raise


BANNER_URL = ENV.get("BANNER_URL", "")  # optional public image shown at the bottom of the card


def card(room, display, via_obs, link):
    """The fancy message: a coloured embed with fields, a live timestamp and a link button."""
    now = int(time.time())
    embed = {
        "title": f"\U0001F534 {display} is live",
        "url": link,
        "description": "Sharing their screen. Hit **Watch** to jump in!",
        "color": 0xEB459E if via_obs else 0x5865F2,
        "fields": [
            {"name": "\U0001F4FA Source", "value": "\U0001F3AE OBS" if via_obs else "\U0001F310 Browser", "inline": True},
            {"name": "\U0001F552 Started", "value": f"<t:{now}:R>", "inline": True},
            {"name": "\U0001F6AA Room", "value": f"`{room}`", "inline": True},
        ],
        "footer": {"text": "Needs ZeroTier \u00b7 this message disappears when the stream ends"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now)),
    }
    if BANNER_URL:
        embed["image"] = {"url": BANNER_URL}
    return {
        "embeds": [embed],
        "components": [{"type": 1, "components": [
            {"type": 2, "style": 5, "label": "Watch", "emoji": {"name": "\U0001F4FA"}, "url": link},
        ]}],
        "allowed_mentions": {"parse": []},
    }


def plain(display, via_obs, link):
    how = " (OBS)" if via_obs else ""
    return {
        "content": f"\U0001F3A5 **{display}** is sharing their screen{how}: {link}\n(needs ZeroTier; this message disappears when they stop)",
        "allowed_mentions": {"parse": []},
    }


def share_started(room, identity, display, via_obs):
    with LOCK:
        state = load()
        k = key(room, identity)
        if k in state:
            return
        link = f"{MEET_URL}/rooms/{urllib.parse.quote(room, safe='')}"
        try:
            msg = discord("POST", f"/channels/{CHANNEL}/messages", card(room, display, via_obs, link))
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            log(f"fancy card rejected ({e.read()[:150]!r}), sending the plain message instead")
            msg = discord("POST", f"/channels/{CHANNEL}/messages", plain(display, via_obs, link))
        state[k] = msg["id"]
        save(state)
        log(f"posted message {msg['id']} for {identity} in {room}")


def share_stopped(room, identity=None):
    """Delete the message of one sharer, or of everybody in the room when identity is None."""
    with LOCK:
        state = load()
        keys = [k for k in state if k.startswith(room + "\t") and (identity is None or k == key(room, identity))]
        for k in keys:
            delete_message(state[k])  # raises on failure: state stays, LiveKit retries
            log(f"deleted message {state.pop(k)} for {k.split(chr(9), 1)[1]} in {room}")
            save(state)


def handle(ev):
    """Screen shares: OBS (participant identity 'obs-<name>', video track) or a browser screen-share track."""
    event = ev.get("event")
    room = (ev.get("room") or {}).get("name", "")
    p = ev.get("participant") or {}
    ident = p.get("identity", "")
    track = ev.get("track") or {}
    if not room:
        return
    if event == "room_finished":
        return share_stopped(room)
    if not ident or ident.startswith("obs-test"):  # test streams (name starting with "test") are never announced
        return
    via_obs = ident.startswith("obs-")
    is_share_track = track.get("type") in ("VIDEO", 1) and (via_obs or track.get("source") in ("SCREEN_SHARE", 3))
    if event == "track_published" and is_share_track:
        display = (p.get("name") or ident).removesuffix(" (OBS)")
        share_started(room, ident, display, via_obs)
    elif event == "track_unpublished" and is_share_track:
        share_stopped(room, ident)
    elif event == "participant_left":
        share_stopped(room, ident)


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
            handle(ev)
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
