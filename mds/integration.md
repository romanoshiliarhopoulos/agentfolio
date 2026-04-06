# Chat Integration — Analysis & Design

How to connect agentfolio to a messaging channel so you can receive reports,
get escalation alerts, and query your portfolio by sending a message.

---

## What the integration needs to do

Two distinct flows:

**Outbound (push)** — the system messages you:
- Weekly report delivered after agent5 completes (Sunday ~23:30)
- Escalation alert if pulse check returns `ESCALATE` (Wednesday morning)
- Optional: daily scout summary if not a quiet day

**Inbound (query)** — you message the system:
- "show report" → returns this week's markdown report
- "nav" or "holdings" → returns current snapshot values
- "status" → returns which agents have run this week
- "scout" → returns today's or latest scout log
- Free-text question → Claude answers using the latest snapshot as context

---

## Option 1: Email (Gmail)

### How it works
- **Outbound**: Python sends email via SMTP (Gmail App Password or SendGrid).
  Agent5 and the pulse script call a `notify.py` module after writing their outputs.
- **Inbound**: A polling script runs every 5 minutes via launchd, checks an inbox
  via IMAP, parses the subject line as a command, and replies.

### Architecture
```
agent5 finishes
    └─▶ notify.py send_report()
            └─▶ Gmail SMTP → your inbox

You reply "holdings"
    └─▶ poll_inbox.py (every 5 min, launchd)
            └─▶ IMAP fetch unread from your address
            └─▶ parse subject → dispatch command
            └─▶ build reply (from snapshot JSON or run Claude)
            └─▶ SMTP reply to same thread
```

### Setup cost
- Gmail account (existing) + App Password (2-min setup in Google Account settings)
- No external service, no monthly cost
- `smtplib` and `imaplib` are Python stdlib — zero new dependencies

### Limitations
- 5-minute polling delay on inbound queries
- Gmail IMAP rate limits if you poll too aggressively (use exponential backoff)
- Email is not great for quick back-and-forth; better for reports than queries
- Markdown renders poorly in most email clients (need HTML conversion)
- Requires an always-on polling process, which is a second launchd job

### Best for
Receiving the weekly report and escalation alerts. Not great for interactive querying.

---

## Option 2: WhatsApp

### Sub-option A: WhatsApp Business API via Twilio
The only fully supported route. Meta's own API requires a verified business account,
which takes days and needs a business entity. Twilio wraps the same API with easier setup.

**Setup cost**:
- Twilio account + WhatsApp sandbox (~$15/mo + per-message cost ~$0.005)
- Meta Business verification (if going official route)
- Webhook endpoint — requires a public URL (ngrok locally, or a small VPS/Lambda)
- Cannot message your personal WhatsApp number directly; you connect a sandbox number

**Architecture**:
```
agent5 finishes
    └─▶ notify.py → Twilio REST API → WhatsApp sandbox → your phone

You send "nav"
    └─▶ WhatsApp → Twilio webhook → your public endpoint → parse & respond
```

**Major problem**: the webhook requires an internet-accessible server. Your Mac at home
is not reachable from the internet without port forwarding or a cloud proxy. This means
you either need to run a small cloud function (AWS Lambda, Fly.io) just to relay
webhook events, which significantly increases complexity.

### Sub-option B: Unofficial libraries (pywhatkit, whatsapp-web.js)
These automate the WhatsApp Web browser interface. They are:
- Against WhatsApp's Terms of Service — your number can be banned
- Fragile (break on WhatsApp UI updates)
- Require a persistent browser session and your phone to be nearby/connected

**Not recommended for a production system.**

### Best for
WhatsApp is the most convenient delivery channel for someone already using it,
but the webhook infrastructure requirement makes it significantly more complex
than the alternatives for a home-run Mac system.

---

## Option 3: Telegram Bot (Recommended)

### Why Telegram wins for this use case
- **No webhook required**: Telegram supports long-polling (`getUpdates`), meaning
  your Mac polls Telegram's servers outbound — no public IP, no port forwarding needed
- **Free**: no per-message cost, no business verification
- **Bot API is excellent**: simple REST calls, supports markdown formatting natively,
  can send files, supports message threading
- **Instant**: messages arrive in seconds
- **Your phone already has it** (or you can install it)
- Bot takes 2 minutes to create via @BotFather

### Architecture
```
agent5 finishes
    └─▶ notify.py → Telegram Bot API (sendMessage) → your phone

You send "nav"
    └─▶ Telegram server (holds the message)
            └─▶ poll_telegram.py (every 30s, launchd) → getUpdates
            └─▶ parse command
            └─▶ build reply (snapshot JSON or Claude)
            └─▶ Telegram Bot API (sendMessage) → your phone
```

### Setup
1. Message @BotFather on Telegram → `/newbot` → get `BOT_TOKEN`
2. Message your bot once (to get your `CHAT_ID`)
3. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_CHAT_ID=<your_chat_id>
   ```

### Message formatting
Telegram supports MarkdownV2. The weekly report from agent5 can be sent near-verbatim,
or trimmed to the key sections (Snapshot + Recommendations + Next Steps) to fit
Telegram's 4096-character message limit. Full report sent as a `.md` file attachment.

---

## Comparison

| | Email | WhatsApp (Twilio) | Telegram |
|---|---|---|---|
| Setup time | ~30 min | ~2–4 hours + verification | ~10 min |
| Monthly cost | Free | ~$15 + per-msg | Free |
| Requires public endpoint | No | **Yes** | No |
| Inbound queries | Yes (5min delay) | Yes (instant) | Yes (30s delay) |
| Markdown rendering | Poor (need HTML) | Limited | Native |
| Send files (reports) | Yes | Yes | Yes |
| Reliability | High | High | High |
| ToS risk | None | None | None |
| **Verdict** | Good for reports only | Overkill for home setup | **Best fit** |

---

## Recommended Design: Telegram

Two new files added to the project:

### `src/notify.py`
Handles all outbound messages. Called by agent5 and the pulse script after they write
their outputs.

```
notify.send_report(report_path)      # sends report markdown + summary
notify.send_escalation(pulse_result) # sends ESCALATE alert with details
notify.send_quiet_scout()            # optional: daily quiet-day confirmation
```

### `src/bot.py`
Polls Telegram for inbound commands and responds. Runs as a long-polling loop,
started by a launchd job that keeps it alive.

**Supported commands**:

| Command | Response |
|---------|----------|
| `/report` | Latest weekly report (sent as file + key sections inline) |
| `/nav` | Current NAV, cash, invested from snapshot |
| `/holdings` | Holdings table with PnL |
| `/scout` | Latest scout log |
| `/status` | Which agents have run this week + timestamps |
| `/pulse` | Latest pulse check verdict |
| `/ask <question>` | Claude answers using current snapshot as context (1 turn) |
| `/risk` | Agent 2 risk score + key risks summary |
| `/help` | Lists commands |

### `/ask` design
The `/ask` command is the most powerful. When you send `/ask is now a good time to add
to my VUAA position?`, `bot.py` loads the latest snapshot and passes it with your
question to `claude -p` (1 turn, ~2k token context). This uses your Pro budget
(1 message per question) but gives you on-demand portfolio intelligence at any time
of day.

The system prompt for `/ask` is a condensed version of the investor profile:
> "You are answering a quick question for the investor. Use the portfolio data provided.
> Be direct. Max 3 sentences unless a table adds value. No disclaimers."

---

## Integration Points in Existing Code

Three places need a `notify.py` call added:

**`src/agents/agent5.py`** — after `write_text(out_path, output)`:
```python
from notify import send_report
send_report(out_path)
```

**`src/agents/agent_pulse.py`** — after `write_json(out_path, result)`:
```python
from notify import send_escalation
if result.get("verdict") == "ESCALATE":
    send_escalation(result)
```

**`scripts/run_scout.sh`** — optional, after scout completes:
```bash
"$PYTHON" src/notify.py --scout   # only sends if not a quiet day
```

---

## launchd for bot.py

`bot.py` needs to stay running to poll Telegram. Add a `KeepAlive` launchd job:

```xml
<!-- com.agentfolio.bot.plist -->
<key>KeepAlive</key>
<true/>
<key>ProgramArguments</key>
<array>
    <string>/Users/romanos/agentfolio/.venv/bin/python</string>
    <string>/Users/romanos/agentfolio/src/bot.py</string>
</array>
```

With `KeepAlive true`, launchd automatically restarts `bot.py` if it crashes.

---

## Implementation Order

1. **Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `.env`**
   — Get bot token from @BotFather, get your chat ID by messaging the bot and calling
   `https://api.telegram.org/bot<TOKEN>/getUpdates`

2. **Write `src/notify.py`** — outbound only first (send report, send escalation).
   Test by running agent5 manually and confirming the report arrives on Telegram.

3. **Add notify calls to agent5 and agent_pulse** — two one-liners each.

4. **Write `src/bot.py`** — inbound polling. Start with `/nav`, `/holdings`, `/report`.
   Add `/ask` last (it uses Claude budget).

5. **Add `com.agentfolio.bot.plist`** to launchd.

Dependencies needed (add via `poetry add`):
```
poetry add requests   # already present — used for Telegram REST calls
```
No new dependencies. Telegram Bot API is pure REST over HTTPS.
