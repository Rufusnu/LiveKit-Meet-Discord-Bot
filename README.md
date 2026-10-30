# LiveKit Meet Discord Bot

A tiny bot that announces [LiveKit](https://livekit.io) rooms in a Discord channel.

- When a room **starts**, it posts a message with a link to the room.
- When the room **finishes**, it deletes that message.

It is one Python file using only the standard library (no pip packages), written for a self-hosted
LiveKit server with the [LiveKit Meet](https://github.com/livekit-examples/meet) front end. It works
with any LiveKit server that can send webhooks, including LiveKit Cloud.

```
LiveKit server --(webhook: room_started / room_finished)--> bot.py --(Discord REST API)--> #channel
```

## How it works

1. LiveKit sends a signed webhook (`room_started`, `room_finished`) to the bot.
2. The bot checks the JWT in the `Authorization` header (HS256 with your API secret, issuer = your API
   key, not expired, and the `sha256` claim matches the request body). Anything else gets `401`.
3. On `room_started` it posts `Room <name> is open: <MEET_URL>/rooms/<name>` and saves
   `room name -> message id` in `state.json`. A second start for the same room is ignored.
4. On `room_finished` it deletes that message and forgets the room. If the message was already deleted
   by someone, that is fine. If Discord fails, the bot answers `500` so LiveKit retries.

The message disables mentions (`allowed_mentions: {parse: []}`), so a room name like `@everyone` cannot ping anyone.

## Requirements

- Python 3.9+ (standard library only)
- A LiveKit server with webhooks enabled and the Meet app (or anything that serves `/rooms/<name>`)
- A Discord bot token and a channel id

## Setup

### 1. Create the Discord bot

1. Go to <https://discord.com/developers/applications>, **New Application**, then **Bot**, and copy the token.
2. Invite it to your server (OAuth2 > URL Generator, scope `bot`) with only **View Channel** and
   **Send Messages**. A bot can delete its own messages without any extra permission.
3. In Discord enable Developer Mode, right-click the channel and **Copy Channel ID**.

### 2. Configure the bot

```bash
sudo useradd --system --home /opt/livekit-bot --shell /usr/sbin/nologin livekitbot
sudo mkdir -p /opt/livekit-bot && sudo chown livekitbot:livekitbot /opt/livekit-bot
sudo cp bot.py /opt/livekit-bot/bot.py
sudo cp .env.example /opt/livekit-bot/.env      # then fill it in
sudo chown root:livekitbot /opt/livekit-bot/.env && sudo chmod 640 /opt/livekit-bot/.env
sudo cp livekit-discord-bot.service /etc/systemd/system/
```

`.env` values:

| Variable | Meaning |
|---|---|
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | The key pair LiveKit signs webhooks with (same pair as in `livekit.yaml`) |
| `MEET_URL` | Public base URL of your Meet page, e.g. `https://meet.example.com` |
| `DISCORD_CHANNEL_ID` | Channel to post in |
| `DISCORD_TOKEN` | Bot token. Keep it secret, never commit it |

Optional: `PORT` (default `8099`), `STATE_FILE` (default `/opt/livekit-bot/state.json`).

### 3. Tell LiveKit where to send webhooks

In `livekit.yaml` on the LiveKit server:

```yaml
webhook:
  api_key: <the same LIVEKIT_API_KEY>
  urls:
    - http://127.0.0.1:8099/
```

Restart LiveKit. If LiveKit runs in Docker with `network_mode: host`, `127.0.0.1` reaches the bot. Otherwise
use an address the LiveKit container can reach. The bot listens on `127.0.0.1` only, so it is not exposed.

### 4. Start it

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now livekit-discord-bot
journalctl -u livekit-discord-bot -f
```

Open a room in Meet: the message should appear. Leave and wait for the room to close: it is deleted.

> LiveKit closes a room after it has been empty for its `empty_timeout` (5 minutes by default), so the
> message disappears a few minutes after the last person leaves.

## Tests

`test_bot.py` starts the bot against a fake Discord server and sends signed webhooks. It needs no network
and no real token:

```bash
python3 test_bot.py
```

It checks posting, duplicate starts, deleting, no-op for unknown rooms, and that wrong-secret, wrong-key and
expired signatures are rejected.

## Notes and limits

- The room link works only for people who can reach your Meet page (for example only over a VPN such as ZeroTier).
- State lives in one JSON file. If you delete it while rooms are open, those messages will not be removed
  automatically.
- LiveKit does not guarantee webhook delivery (it retries a few times). A missed `room_finished` leaves the
  message in the channel; delete it by hand and remove the room from `state.json`.
- One channel, one bot, no slash commands: it only reacts to LiveKit events.
