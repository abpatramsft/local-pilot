# Local Pilot — Agent Chat UI powered by GitHub Copilot SDK

A self-hosted agent chat application that wraps the **GitHub Copilot SDK** behind a Flask server and exposes it through **ngrok** so the chat UI can be accessed from any device, anywhere.

```
┌──────────────┐      ngrok tunnel       ┌────────────────┐      Copilot SDK      ┌───────────┐
│  Browser /   │  ◄──────────────────►   │  Flask server  │  ◄────────────────►   │  GitHub   │
│  Mobile      │   (public HTTPS URL)    │  (localhost)   │   sessions & tools    │  Copilot  │
└──────────────┘                          └────────────────┘                       └───────────┘
```

## Features

- **Remote access via ngrok** — run the server locally, get a public HTTPS URL, and chat from your phone/tablet/any browser on earth
- **Streaming responses** — real-time Server-Sent Events (SSE) streaming with tool-call visibility (start/complete/error)
- **Skills** — add skill directories under `skills/` with a `SKILL.md` to give the agent domain-specific knowledge (code review, security audit, testing, docs writing)
- **Local session browser** — fetch, view, and resume past Copilot CLI sessions directly from the UI
- **Single-file UI** — a dark-themed, mobile-responsive chat interface in one `index.html` — no build step required
- **Workspace sandbox** — file operations default to the `pilot_folder/` directory for safety

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | For the Flask backend |
| **GitHub Copilot access** | A valid GitHub token with Copilot entitlements |
| **ngrok** | Free account at [ngrok.com](https://ngrok.com) — install via `brew install ngrok` or [download](https://ngrok.com/download) |

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `flask` + `flask-cors` — API server
- `github-copilot-sdk` — Copilot SDK for Python
- `pydantic` — data validation

### 2. Configure authentication

The server picks up a GitHub token from environment variables (checked in order):

```bash
# Option A: Copilot-specific token
export COPILOT_GITHUB_TOKEN="ghp_..."

# Option B: Standard GitHub CLI token
export GH_TOKEN="ghp_..."

# Option C: Generic
export GITHUB_TOKEN="ghp_..."
```

If none are set, the SDK falls back to the logged-in GitHub CLI user (`gh auth status`).

### 3. Start the Flask server

```bash
python app.py
```

The server starts on **port 5000** by default (override with `PORT` env var):

```
  Agent chat server running → http://localhost:5000
```

### 4. Expose with ngrok

In a separate terminal, run ngrok with your custom domain:

```bash
ngrok http --domain=your-custom-subdomain.ngrok-free.dev 5000
```

> You get a free static domain from the [ngrok dashboard](https://dashboard.ngrok.com/domains) — this gives you a stable URL that doesn't change between restarts.

ngrok prints output like:

```
Forwarding  https://your-custom-subdomain.ngrok-free.dev → http://localhost:5000
```

### 5. Open the UI

Open `index.html` in any browser (locally or on another device). In the sidebar footer, paste the ngrok URL into the **agent endpoint** field:

```
https://abcd-1234.ngrok-free.app/chat
```

The green dot next to "agent endpoint" lights up when the backend is reachable. Hit **+ New Session** and start chatting.

> **Tip**: You can also host `index.html` on any static file server or open it directly as a `file://` — CORS is enabled on the Flask side.

## Project Structure

```
local-pilot/
├── app.py                 # Flask server — REST + SSE endpoints
├── agent.py               # Copilot SDK wrapper — session management, streaming
├── local_sessions.py      # Fetch & browse past Copilot CLI sessions
├── whatsapp.py            # WhatsApp integration via Twilio webhooks
├── twilio_config.py       # Twilio credentials (⚠ do not commit)
├── index.html             # Self-contained chat UI (HTML + CSS + JS)
├── requirements.txt       # Python dependencies
├── skills/                # Skill directories (each has a SKILL.md)
│   ├── code-review/
│   ├── docs-writer/
│   ├── security-audit/
│   └── testing/
└── pilot_folder/          # Default workspace for file operations
    └── src/
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |
| `POST` | `/chat` | Send a message, get a full reply (non-streaming) |
| `POST` | `/chat/stream` | Send a message, receive SSE stream with deltas + tool events |
| `GET` | `/skills` | List available skills |
| `GET` | `/local-sessions` | List previously fetched Copilot CLI sessions |
| `POST` | `/local-sessions/fetch` | Trigger a fresh fetch of sessions from Copilot CLI |
| `GET` | `/local-sessions/<id>` | Get full conversation for a local session |

## Skills

Add a directory under `skills/` with a `SKILL.md` file:

```markdown
---
name: Code Review
description: Provides structured code review with quality scoring
---

# Code Review Skill

When performing code reviews, follow this structured approach...
```

Skills appear in the **# Skills** dropdown and inject domain-specific instructions into the agent.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `PORT` | `5000` | Flask server port |
| `COPILOT_WORKSPACE` | `./pilot_folder` | Working directory for file operations |
| `COPILOT_GITHUB_TOKEN` | — | GitHub token for Copilot SDK |
| `GH_TOKEN` | — | Fallback GitHub token |
| `GITHUB_TOKEN` | — | Second fallback GitHub token |

## WhatsApp Integration (via Twilio)

You can chat with the agent from WhatsApp using the Twilio sandbox — same skills, sessions, all from your phone.

### Setup

#### Step 1 — Create a free Twilio account

1. Sign up at [twilio.com](https://www.twilio.com/) (no credit card required for sandbox)
2. From the Twilio Console dashboard, note your **Account SID** and **Auth Token** (under "Account Info")

#### Step 2 — Activate the WhatsApp sandbox

1. In Twilio Console, go to **Messaging → Try it out → Send a WhatsApp message**
2. You'll see a sandbox number (+1 415 523 8886) and a join code (e.g. *"join similar-mostly"*)
3. From your phone, open WhatsApp and send that exact join code to **+1 415 523 8886**
4. Wait for the confirmation reply: *"You are all set!"*

> Each phone number that wants to use the bot must send the join code to opt in.

#### Step 3 — Configure the webhook URL

1. In Twilio Console, go to **Messaging → Try it out → Send a WhatsApp message → Sandbox settings** tab
2. In the **"When a message comes in"** field, enter your ngrok URL with the `/whatsapp` path:
   ```
   https://your-domain.ngrok-free.dev/whatsapp
   ```
3. Set Method to **POST**
4. Leave "Status callback URL" empty
5. Click **Save**

> If you skip this step, Twilio replies with a default "You said: ..." echo message instead of routing to your agent.

#### Step 4 — Create `twilio_config.py`

This file is **not included in the repository** (it's in `.gitignore` because it contains secrets). You must create it yourself in the project root:

```bash
touch twilio_config.py
```

Then add the following content with your own Twilio credentials:

```python
"""
twilio_config.py — Twilio credentials for WhatsApp integration.

Fill in your values from https://console.twilio.com/
"""

# Find these at https://console.twilio.com/ (Dashboard → Account Info)
TWILIO_ACCOUNT_SID = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"   # your Account SID
TWILIO_AUTH_TOKEN   = "your_auth_token_here"                 # your Auth Token

# Your Twilio WhatsApp sandbox number (default sandbox number shown)
TWILIO_WHATSAPP_FROM = "whatsapp:+14155238886"
```

> **⚠ Do not commit this file** — it contains secrets and is excluded via `.gitignore`.

#### Step 5 — Install dependencies & restart

```bash
pip install -r requirements.txt   # installs twilio alongside other deps
python app.py
```

You should see in the terminal:
```
[WhatsApp] ✓ Twilio configured — from whatsapp:+14155238886
   [WhatsApp] ✓ /whatsapp endpoint registered
   ```

### WhatsApp Commands

| Command | Action |
|---|---|
| *(any text)* | Chat with the agent |
| `/skills` | List available skills |
| `/use #code-review #testing` | Select skills for your session |
| `/config` | Show current session config |
| `/sessions` | List recent local Copilot sessions |
| `/resume <id>` | Resume a past session |
| `/new` | Start a fresh session |
| `/help` | Show command list |

### How It Works

- Each phone number gets its own session state (history, selected skills)
- If the agent replies within ~12 seconds, the response is returned inline
- If it takes longer, you get a "⏳ Thinking..." message and the real reply is delivered asynchronously via the Twilio REST API
- Replies are truncated to ~1500 characters to stay within WhatsApp limits

## ngrok Tips

- **Custom subdomain** (paid plans): `ngrok http 5000 --subdomain=my-pilot` gives you a stable URL
- **Auth protection**: `ngrok http 5000 --basic-auth="user:password"` adds HTTP basic auth
- **Inspect traffic**: visit `http://127.0.0.1:4040` while ngrok is running to see all requests/responses
- The UI sends `ngrok-skip-browser-warning: 1` header automatically so you won't see the ngrok interstitial page

## License

Private / Internal use.
