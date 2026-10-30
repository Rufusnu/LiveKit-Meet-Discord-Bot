import base64, hashlib, hmac, json, os, subprocess, sys, tempfile, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
calls = []
class D(BaseHTTPRequestHandler):
    def _h(self):
        n = int(self.headers.get("Content-Length", 0)); b = self.rfile.read(n)
        calls.append((self.command, self.path, json.loads(b) if b else None, self.headers.get("Authorization")))
        out = json.dumps({"id": "m%d" % len(calls)}).encode() if self.command == "POST" else b""
        self.send_response(200 if out else 204); self.end_headers(); self.wfile.write(out)
    do_POST = do_DELETE = _h
    def log_message(self, *a): pass
threading.Thread(target=HTTPServer(("127.0.0.1", 8098), D).serve_forever, daemon=True).start()
KEY, SEC = "TESTKEY", "testsecret"
d = tempfile.mkdtemp()
env = dict(os.environ, DISCORD_TOKEN="faketoken", DISCORD_CHANNEL_ID="123", LIVEKIT_API_KEY=KEY, MEET_URL="https://meet.example.com", LIVEKIT_API_SECRET=SEC,
           DISCORD_API="http://127.0.0.1:8098", STATE_FILE=d + "/state.json", PORT="8097")
p = subprocess.Popen([sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")], env=env); time.sleep(1.5)
def b64(x): return base64.urlsafe_b64encode(x).rstrip(b"=")
def jwt(body, key=KEY, sec=SEC, exp=3600):
    h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    pl = b64(json.dumps({"iss": key, "exp": int(time.time()) + exp, "sha256": base64.b64encode(hashlib.sha256(body).digest()).decode()}).encode())
    sig = b64(hmac.new(sec.encode(), h + b"." + pl, hashlib.sha256).digest())
    return (h + b"." + pl + b"." + sig).decode()
def send(ev, room, **kw):
    body = json.dumps({"event": ev, "room": {"name": room}}).encode()
    r = urllib.request.Request("http://127.0.0.1:8097/", data=body, method="POST", headers={"Authorization": jwt(body, **kw)})
    try: return urllib.request.urlopen(r).status
    except urllib.error.HTTPError as e: return e.code
res = {}
res["start ok"] = send("room_started", "game night") == 200
res["dup start ignored"] = send("room_started", "game night") == 200 and len(calls) == 1
c = calls[0]
res["posted to channel 123 with bot auth"] = c[1] == "/channels/123/messages" and c[3] == "Bot faketoken"
res["link in message"] = "https://meet.example.com/rooms/game%20night" in c[2]["content"]
res["no mentions allowed"] = c[2]["allowed_mentions"] == {"parse": []}
res["wrong secret rejected (401)"] = send("room_finished", "game night", sec="wrong") == 401
res["wrong key rejected (401)"] = send("room_finished", "game night", key="OTHER") == 401
res["expired rejected (401)"] = send("room_finished", "game night", exp=-10) == 401
res["no delete after rejects"] = len(calls) == 1
res["finish ok"] = send("room_finished", "game night") == 200
res["deleted message m1"] = calls[-1][0] == "DELETE" and calls[-1][1] == "/channels/123/messages/m1"
res["finish unknown room is a no-op"] = send("room_finished", "ghost") == 200 and len(calls) == 2
res["state empty"] = json.load(open(d + "/state.json")) == {}
p.terminate()
for k, v in res.items(): print("PASS" if v else "FAIL", k)
sys.exit(0 if all(res.values()) else 1)
