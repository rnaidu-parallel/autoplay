# Twitch chat agent

The chat agent reads Twitch chat, filters unsafe or excessive input, groups explicit gameplay requests, and maintains a decaying vote table. It selects at most one request per in-game day. The harness converts the selected request into a verified agenda item. Chat never supplies a tool call or success condition.

Shadow mode is the default. It writes the selected request to `chat/state.json` and shows it in the overlay, but it does not steer the game.

## Run a shadow session

1. Install the dependencies declared in `harness/pyproject.toml` into the Python environment used by the harness.
2. Set `OPENROUTER_API_KEY` and `TWITCH_CHANNEL` in `.env`.
3. Start the game harness and overlay.
4. Start the chat agent:

```powershell
.\chat\run-chat.ps1 --transport irc --mode shadow
```

The anonymous IRC transport needs no Twitch token. Review `chat/state.json` after the stream. Do not use binding mode until one shadow stream is reviewed.

## Use EventSub

EventSub is the preferred authenticated transport for a locally hosted Twitch chat client. Create a Twitch user access token with `user:read:chat`. Set these values in `.env`:

```text
TWITCH_CLIENT_ID=
TWITCH_ACCESS_TOKEN=
TWITCH_BROADCASTER_ID=
TWITCH_USER_ID=
```

Then run:

```powershell
.\chat\run-chat.ps1 --transport eventsub --mode shadow
```

The process reconnects and recreates the subscription after an ordinary disconnect. Twitch can deliver an EventSub notification more than once, so the process deduplicates message IDs. See [Twitch EventSub WebSockets](https://dev.twitch.tv/docs/eventsub/handling-websocket-events/) and [chat authentication](https://dev.twitch.tv/docs/chat/authenticating/).

## Enable binding after the shadow gate

Run:

```powershell
.\chat\run-chat.ps1 --transport eventsub --mode bind
```

The agent submits only the winning goal and its unique supporter count. The harness schedules a dedicated director planning pass without discarding the actor's in-flight decision. A second request on the same game day is rejected by both the chat agent and the harness.

## Enable Twitch replies

Add `user:write:chat` to the Twitch user token. Then add `--reply`:

```powershell
.\chat\run-chat.ps1 --transport eventsub --mode bind --reply
```

The agent replies when the request enters Neon's plan and when it completes, fails, or misses the plan. Twitch limits messages to 500 characters; the sender truncates at that limit. See [Send Chat Message](https://dev.twitch.tv/docs/chat/send-receive-messages/).

## Configure filtering

Create the ignored local file `chat/blocklist.txt`. Add one blocked phrase per line. The built-in filter also rejects links, prompt-injection-shaped text, messages longer than 500 characters, and messages above the per-user window cap.

Use `--window-seconds`, `--half-life-seconds`, `--min-support`, and `--per-user-cap` only after shadow-run evidence shows that the defaults are unsuitable.
