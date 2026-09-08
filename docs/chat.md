# Chat agent (Twitch and Kick)

The chat agent reads Twitch and Kick chat, filters unsafe or excessive input, groups explicit requests, and maintains a decaying vote table. It submits one agreed request at a time to the farmer, and the next as soon as he has settled the previous one. It also keeps the last ten minutes of accepted lines so the farmer can read and answer chat. Chat never supplies a tool call or success condition.

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

## Read Kick chat too

Kick chat is read over Kick's public Pusher websocket; no Kick account is needed.

1. Set `KICK_CHANNEL` in `.env` to the channel slug, or pass `--kick-channel <slug>`.
2. If Kick refuses the slug lookup (its front door sometimes blocks scripts), open
   `https://kick.com/api/v2/channels/<slug>` in a browser, copy `chatroom.id`, and set
   `KICK_CHATROOM_ID` instead.
3. Run the agent with both `--channel` and `--kick-channel` to merge the two chats. Kick message
   and user ids are prefixed `kick:` so they never collide with Twitch's.

The agent keeps the last ten minutes of accepted lines in `chat/state.json`; the farmer sees up to
six of them in every observation and may answer them. Requests are still extracted and voted on;
the next agreed request is submitted as soon as the farmer has settled the previous one, so chat
can ask for several things in one game day.

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

## Let Neon answer chat

Any action Neon takes can carry a `chat` line: something he says to the viewers, as opposed to
`say`, which is his narration on the overlay. With `--reply`, the chat agent posts every `chat`
line as "Neon: ..." on every platform it has credentials for, at most one line per platform every
twenty seconds, and never a line older than two minutes. Lines written before the agent started are
not replayed. Request outcomes ("Added to Neon's plan: ...") go to the same platforms.

Twitch replies use the bot account described below. For Kick:

1. Register an app at Kick's developer settings with redirect URI `http://localhost:8790/callback`
   and the `chat:write` scope; put `KICK_CLIENT_ID` and `KICK_CLIENT_SECRET` in `.env`, and the
   channel slug (hyphens, as in the channel URL) in `KICK_CHANNEL`.
2. Run `.\chat\run-chat.ps1 --kick-login` and approve in the browser tab that opens. Lines appear
   in chat as whichever account approved, so approve as a bot account if you want them separate
   from your own. Tokens land in `chat/kick-tokens.json` (git-ignored) and refresh themselves.
3. Start the agent with `--reply`. Lines are posted into the channel named by `KICK_CHANNEL`
   (`KICK_BROADCASTER_USER_ID` skips the lookup). Kick's bot-type post returned a server error
   for us, so the agent posts user-type messages aimed at the broadcaster id.

## Enable Twitch replies

Twitch posts need a user token with `user:write:chat` for the account that speaks. The login works
like the Kick one, except that Twitch's console only takes an https redirect URL, so the local
callback port is exposed through a tunnel for the minute the login takes.

1. Start a tunnel to the login port and note its https address:

   ```powershell
   ngrok http 8791
   ```

2. Register an app at <https://dev.twitch.tv/console/apps> (category Chat Bot) with the OAuth
   redirect URL `https://<your-id>.ngrok.app/callback`. Put its `TWITCH_CLIENT_ID` and
   `TWITCH_CLIENT_SECRET` in `.env`; `TWITCH_CHANNEL` names the channel the lines go to.
3. Run the login with the same redirect URL and approve in the browser tab that opens, signed in as
   the account that should speak (a bot account keeps its lines separate from yours):

   ```powershell
   .\chat\run-chat.ps1 --twitch-login --twitch-redirect https://<your-id>.ngrok.app/callback
   ```

   Tokens, the speaker's id and the channel's broadcaster id land in `chat/twitch-tokens.json`
   (git-ignored); the token refreshes itself. Stop the tunnel afterwards; replies never need it.
4. Start the agent with `--reply`:

```powershell
.\chat\run-chat.ps1 --transport irc --mode bind --reply
```

`TWITCH_ACCESS_TOKEN`, `TWITCH_BROADCASTER_ID` and `TWITCH_USER_ID` in the environment still work
when there is no token file. The agent posts every `chat` line Neon writes, and replies when a request
enters his plan and when it completes, fails, or misses the plan. Twitch limits messages to 500
characters; the sender truncates at that limit. See [Send Chat Message](https://dev.twitch.tv/docs/chat/send-receive-messages/).

## Configure filtering

Create the ignored local file `chat/blocklist.txt`. Add one blocked phrase per line. The built-in filter also rejects links, prompt-injection-shaped text, messages longer than 500 characters, and messages above the per-user window cap.

Chat is processed in windows of `--window-seconds` (default 15, so a line reaches the farmer within
about twenty seconds; the first live steer took over a minute at the old 60). Use `--half-life-seconds`,
`--min-support`, and `--per-user-cap` only after shadow-run evidence shows that the defaults are unsuitable.
