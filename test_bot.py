"""Tests for bot.py against a fake Discord server. No network, no real token. Run: python3 test_bot.py"""
import base64, hashlib, hmac, json, os, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

calls = []
reject_embeds = False


class FakeDiscord(BaseHTTPRequestHandler):
    def _h(self):
        n = int(self.headers.get("Content-Length", 0)); b = self.rfile.read(n)
        calls.append((self.command, self.path, json.loads(b) if b else None, self.headers.get("Authorization")))
        if reject_embeds and self.command == "POST" and "embeds" in calls[-1][2]:
            out = b'{"message": "Invalid Form Body"}'
            self.send_response(400); self.end_headers(); self.wfile.write(out); return
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
e = c[2]["embeds"][0]
LINK = "https://meet.example.com/rooms/main"
src = {f["name"].split(" ", 1)[1]: f["value"] for f in e["fields"]}
res["card title names the sharer without the (OBS) suffix"] = e["title"].endswith("Rufusnu is live") and "(OBS)" not in e["title"]
res["card says the source is OBS, shows room and a live timestamp"] = "OBS" in src["Source"] and src["Room"] == "`main`" and src["Started"].startswith("<t:") and src["Started"].endswith(":R>")
res["card is a link to the room, has footer and timestamp"] = e["url"] == LINK and "ZeroTier" in e["footer"]["text"] and e["timestamp"].endswith("Z")
btn = c[2]["components"][0]["components"][0]
res["Watch link button points to the room"] = btn["type"] == 2 and btn["style"] == 5 and btn["label"] == "Watch" and btn["url"] == LINK
res["OBS and browser cards use different colours"] = True  # checked below
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
e2 = calls[1][2]["embeds"][0]
res["browser screen share posts a card with a Browser source"] = len(calls) == 2 and e2["title"].endswith("Marius is live") and "Browser" in e2["fields"][0]["value"]
res["OBS and browser cards use different colours"] = e["color"] != e2["color"]
send("track_published", "Ana__zz99", "Ana", {"type": 1, "source": 3})
res["numeric enums (type 1, source 3) work"] = len(calls) == 3 and calls[2][2]["embeds"][0]["title"].endswith("Ana is live")
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
n0 = len(calls)
send("track_published", "obs-test-e2e", "test-e2e (OBS)", VIDEO_CAMERA)
res["streams named test* are never announced"] = len(calls) == n0
reject_embeds = True
n = len(calls)
send("track_published", "Zed__0001", "Zed", VIDEO_SCREEN)
res["if Discord rejects the card (400) the plain message is sent instead"] = len(calls) == n + 2 and "embeds" in calls[n][2] and "content" in calls[n + 1][2] and "**Zed**" in calls[n + 1][2]["content"]
p.terminate()
for k, v in res.items(): print("PASS" if v else "FAIL", k)
sys.exit(0 if all(res.values()) else 1)
