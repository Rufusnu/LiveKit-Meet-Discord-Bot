"""Tests for bot.py against a fake Discord server. No network, no real token. Run: python3 test_bot.py"""
import base64, hashlib, hmac, json, os, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

calls = []


class FakeDiscord(BaseHTTPRequestHandler):
    def _h(self):
        n = int(self.headers.get("Content-Length", 0)); b = self.rfile.read(n)
        calls.append((self.command, self.path, json.loads(b) if b else None, self.headers.get("Authorization")))
        out = json.dumps({"id": "m%d" % len(calls)}).encode() if self.command == "POST" else b""
        self.send_response(200 if out else 204); self.end_headers(); self.wfile.write(out)
    do_POST = do_DELETE = _h
    def log_message(self, *a): pass


threading.Thread(target=HTTPServer(("127.0.0.1", 8098), FakeDiscord).serve_forever, daemon=True).start()
KEY, SEC = "TESTKEY", "testsecret"
d = tempfile.mkdtemp()
env = dict(os.environ, DISCORD_TOKEN="faketoken", DISCORD_CHANNEL_ID="123", LIVEKIT_API_KEY=KEY, LIVEKIT_API_SECRET=SEC,
           MEET_URL="https://meet.example.com", DISCORD_API="http://127.0.0.1:8098", STATE_FILE=d + "/state.json", PORT="8097")
p = subprocess.Popen([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")], env=env)
time.sleep(1.5)


def b64(x): return base64.urlsafe_b64encode(x).rstrip(b"=")


def jwt(body, key=KEY, sec=SEC, exp=3600):
    h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    pl = b64(json.dumps({"iss": key, "exp": int(time.time()) + exp, "sha256": base64.b64encode(hashlib.sha256(body).digest()).decode()}).encode())
    return (h + b"." + pl + b"." + b64(hmac.new(sec.encode(), h + b"." + pl, hashlib.sha256).digest())).decode()


def send(event, identity=None, name=None, track=None, room="main", **kw):
    ev = {"event": event, "room": {"name": room}}
    if identity:
        ev["participant"] = {"identity": identity, "name": name or identity}
    if track:
        ev["track"] = track
    body = json.dumps(ev).encode()
    r = urllib.request.Request("http://127.0.0.1:8097/", data=body, method="POST", headers={"Authorization": jwt(body, **kw)})
    try: return urllib.request.urlopen(r).status
    except urllib.error.HTTPError as e: return e.code


VIDEO_CAMERA = {"type": "VIDEO", "source": "CAMERA"}
VIDEO_SCREEN = {"type": "VIDEO", "source": "SCREEN_SHARE"}
AUDIO = {"type": "AUDIO", "source": "MICROPHONE"}
SCREEN_AUDIO = {"type": "AUDIO", "source": "SCREEN_SHARE_AUDIO"}
res = {}

res["OBS video track -> 200"] = send("track_published", "obs-Rufusnu", "Rufusnu (OBS)", VIDEO_CAMERA) == 200
c = calls[0]
res["posted to channel 123 with bot auth"] = len(calls) == 1 and c[0] == "POST" and c[1] == "/channels/123/messages" and c[3] == "Bot faketoken"
res["message names sharer without '(OBS)' suffix, says OBS, has link"] = "**Rufusnu**" in c[2]["content"] and "(OBS)" in c[2]["content"] and "https://meet.example.com/rooms/main" in c[2]["content"]
res["mentions disabled"] = c[2]["allowed_mentions"] == {"parse": []}
send("track_published", "obs-Rufusnu", "Rufusnu (OBS)", AUDIO)
res["OBS audio track ignored"] = len(calls) == 1
send("track_published", "obs-Rufusnu", "Rufusnu (OBS)", VIDEO_CAMERA)
res["duplicate video ignored"] = len(calls) == 1
send("track_published", "Marius__ab12", "Marius", VIDEO_CAMERA)
res["browser camera ignored"] = len(calls) == 1
send("track_published", "Marius__ab12", "Marius", SCREEN_AUDIO)
res["browser screen-share audio ignored"] = len(calls) == 1
send("track_published", "Marius__ab12", "Marius", VIDEO_SCREEN)
res["browser screen share posts, no OBS tag"] = len(calls) == 2 and "**Marius**" in calls[1][2]["content"] and "(OBS)" not in calls[1][2]["content"]
send("track_published", "Ana__zz99", "Ana", {"type": 1, "source": 3})
res["numeric enums (type 1, source 3) work"] = len(calls) == 3 and "**Ana**" in calls[2][2]["content"]
res["wrong secret -> 401"] = send("participant_left", "obs-Rufusnu", sec="wrong") == 401
res["wrong key -> 401"] = send("participant_left", "obs-Rufusnu", key="OTHER") == 401
res["expired -> 401"] = send("participant_left", "obs-Rufusnu", exp=-10) == 401
res["forged requests deleted nothing"] = len(calls) == 3
send("track_unpublished", "Marius__ab12", "Marius", VIDEO_CAMERA)
res["camera unpublish does not delete the share message"] = len(calls) == 3
send("track_unpublished", "Marius__ab12", "Marius", VIDEO_SCREEN)
res["screen share stop deletes that message (m2)"] = calls[-1][:2] == ("DELETE", "/channels/123/messages/m2") and len(calls) == 4
send("participant_left", "obs-Rufusnu", "Rufusnu (OBS)")
res["OBS participant leaving deletes its message (m1)"] = calls[-1][:2] == ("DELETE", "/channels/123/messages/m1") and len(calls) == 5
send("participant_left", "Nobody__0000", "Nobody")
res["unknown participant leaving is a no-op"] = len(calls) == 5
send("room_finished", room="main")
res["room finished deletes the remaining message (Ana's, m3)"] = calls[-1][:2] == ("DELETE", "/channels/123/messages/m3") and len(calls) == 6
res["state empty at the end"] = json.load(open(d + "/state.json")) == {}
send("room_started", room="main")
res["room_started alone posts nothing"] = len(calls) == 6
p.terminate()
for k, v in res.items(): print("PASS" if v else "FAIL", k)
sys.exit(0 if all(res.values()) else 1)
