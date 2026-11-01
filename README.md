# LiveKit Meet Discord Bot

A tiny bot that announces screen shares on a [LiveKit](https://livekit.io) server in a Discord channel.

- When somebody **starts sharing their screen** (with OBS over WHIP, or with the browser's share button), it posts a message with the name of the sharer and a link to the room.
- When they **stop** (or leave, or the room finishes), it deletes that message.

One message per sharer, so several people can share at the same time.

It is one Python file using only the standard library (no pip packages), written for a self-hosted
LiveKit server with the [LiveKit Meet](https://github.com/livekit-examples/meet) front end. It works
with any LiveKit server that can send webhooks, including LiveKit Cloud.

```
LiveKit server --(webhooks: track_published / track_unpublished / participant_left / room_finished)--> bot.py --(Discord REST API)--> #channel
```

## How it works

1. LiveKit sends a signed webhook for every event to the bot.
2. The bot checks the JWT in the `Authorization` header (HS256 with your API secret, issuer = your API
   key, not expired, and the `sha256` claim matches the request body). Anything else gets `401`.
3. A **share** is either a video track published by a participant whose identity starts with `obs-` (OBS through
   LiveKit Ingress/WHIP; cameras of other participants are ignored) or a video track with source `SCREEN_SHARE`
   (the browser's share button). Audio and camera tracks never trigger a message.
4. On `track_published` of a share the bot posts `<name> is sharing their screen: <MEET_URL>/rooms/<room>` and saves
   `room + participant identity -> message id` in `state.json`. A second start for the same participant is ignored.
   A trailing ` (OBS)` in the participant name is shown as "(OBS)" in the sentence.
5. On `track_unpublished` of that share, `participant_left` of that participant, or `room_finished`, it deletes the
   message. If the message was already deleted by someone, that is fine. If Discord fails, the bot answers `500` so
   LiveKit retries.

The message disables mentions (`allowed_mentions: {parse: []}`), so a participant name like `@everyone` cannot ping anyone.

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

Share a screen in Meet (or start OBS): the message should appear. Stop sharing: it is deleted.

## Tests

`test_bot.py` starts the bot against a fake Discord server and sends signed webhooks. It needs no network
and no real token:

```bash
python3 test_bot.py
```

It checks OBS and browser shares, that audio/camera tracks and duplicates are ignored, deleting on stop, leave and
room finish, and that wrong-secret, wrong-key and expired signatures are rejected.

## Notes and limits

- The room link works only for people who can reach your Meet page (for example only over a VPN such as ZeroTier).
- State lives in one JSON file. If you delete it while rooms are open, those messages will not be removed
  automatically.
- LiveKit does not guarantee webhook delivery (it retries a few times). A missed stop event leaves the
  message in the channel; delete it by hand and remove its entry from `state.json`.
- One channel, one bot, no slash commands: it only reacts to LiveKit events.
- OBS participants are recognised by the `obs-` identity prefix, which is how the ingress is created in this setup.
  If you name OBS participants differently, change `via_obs` in `bot.py`.
