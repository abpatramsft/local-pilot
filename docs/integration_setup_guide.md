# Integration Setup Guide

This guide covers setting up the **Microsoft Teams** and **WhatsApp (Twilio)** integrations for local-pilot. Both integrations let you chat with your agent from external platforms — same skills, sessions, models, and commands.

> **Prerequisites**: You should already have the core local-pilot server running (`python app.py`) and exposed via ngrok. See the main [README](../README.md) for initial setup.

---

## Table of Contents

- [Microsoft Teams Integration](#microsoft-teams-integration)
  - [Step 1 — Create an Azure Bot](#step-1--create-an-azure-bot)
  - [Step 2 — Get Your App ID & Password](#step-2--get-your-app-id--password)
  - [Step 3 — Configure Local Credentials](#step-3--configure-local-credentials)
  - [Step 4 — Set the Messaging Endpoint](#step-4--set-the-messaging-endpoint)
  - [Step 5 — Enable the Teams Channel](#step-5--enable-the-teams-channel)
  - [Step 6 — Build & Install the Teams App Package](#step-6--build--install-the-teams-app-package)
  - [Step 7 — Install Dependencies & Restart](#step-7--install-dependencies--restart)
  - [Teams Commands](#teams-commands)
  - [How It Works (Teams)](#how-it-works-teams)
  - [Troubleshooting (Teams)](#troubleshooting-teams)
- [WhatsApp Integration (via Twilio)](#whatsapp-integration-via-twilio)
  - [Step 1 — Create a Free Twilio Account](#step-1--create-a-free-twilio-account)
  - [Step 2 — Activate the WhatsApp Sandbox](#step-2--activate-the-whatsapp-sandbox)
  - [Step 3 — Configure the Webhook URL](#step-3--configure-the-webhook-url)
  - [Step 4 — Create twilio_config.py](#step-4--create-twilio_configpy)
  - [Step 5 — Install Dependencies & Restart](#step-5--install-dependencies--restart)
  - [WhatsApp Commands](#whatsapp-commands)
  - [How It Works (WhatsApp)](#how-it-works-whatsapp)
- [ngrok Tips](#ngrok-tips)

---

## Microsoft Teams Integration

Connect local-pilot to Microsoft Teams as a personal bot. Only you see it — no org-wide installation or IT approval required (as long as custom app sideloading is enabled for your account).

### Step 1 — Create an Azure Bot

1. Go to [portal.azure.com](https://portal.azure.com)
2. Search for **"Azure Bot"** → click **Create**
3. Fill in:
   - **Bot handle**: any unique name (e.g. `local-pilot-bot`)
   - **Pricing tier**: F0 (Free)
   - **Microsoft App ID**: choose _"Create new Microsoft App ID"_
4. Click **Review + Create** → **Create**

### Step 2 — Get Your App ID & Password

1. Open your new Bot resource → click **Configuration** in the sidebar
2. Copy your **Microsoft App ID**
3. Click **Manage Password** → **New client secret** → copy the **Value** immediately (not the Secret ID)

> ⚠️ The secret value is only shown once at creation time. If you lose it, create a new one.

### Step 3 — Configure Local Credentials

Create `teams_config.py` in the project root (already in `.gitignore`):

```python
# teams_config.py
TEAMS_APP_ID       = "your-microsoft-app-id"
TEAMS_APP_PASSWORD = "your-client-secret-VALUE"   # ← the Value, NOT the Secret ID
TEAMS_TENANT_ID    = "your-tenant-id"
```

> **⚠ Do not commit this file** — it contains secrets and is excluded via `.gitignore`.

### Step 4 — Set the Messaging Endpoint

1. In Azure Bot → **Configuration**
2. Set **Messaging endpoint** to:
   ```
   https://YOUR_NGROK_URL/teams
   ```
3. Click **Apply**

> ⚠️ Free ngrok URLs change on every restart. Update this whenever your ngrok URL changes.

### Step 5 — Enable the Teams Channel

1. In Azure Bot → **Channels**
2. Click **Microsoft Teams** → accept terms → **Apply**

### Step 6 — Build & Install the Teams App Package

The `teams-app/` folder contains the manifest and a script to generate your personal app package:

```bash
python teams-app/generate_teams_app.py \
    --app-id YOUR_MICROSOFT_APP_ID \
    --ngrok-url https://YOUR_NGROK_URL
```

This generates `teams-app/local-pilot.zip`.

Then in **Microsoft Teams**:
1. Click **Apps** in the left sidebar
2. Click **Manage your apps** → **Upload an app**
3. Click **Upload a custom app**
4. Select `teams-app/local-pilot.zip`
5. Click **Add**

The bot appears in your Teams **Chat** sidebar as **local-pilot** — only visible to you.

### Step 7 — Install Dependencies & Restart

```bash
pip install -r requirements.txt
python app.py
```

You should see in the terminal:
```
[Teams] ✓ Azure Bot configured — App ID: 4c9dc273... Tenant: 83a49df7...
[Teams] ✓ /teams endpoint registered
```

### Teams Commands

| Command | Action |
|---|---|
| *(any text)* | Chat with the agent |
| `/skills` | List available skills |
| `/mcps` | List available MCP servers |
| `/agents` | List available custom agents |
| `/models` | List available models |
| `/model <id>` | Switch to a specific model (e.g., `/model claude-sonnet-4`) |
| `/use #code-review #testing` | Select skills for your session |
| `/use %workiq` | Select MCP servers for your session |
| `/use @web-search` | Select custom agents for your session |
| `/config` | Show current session config |
| `/sessions` | List recent local Copilot sessions |
| `/resume <id>` | Resume a past session |
| `/new` | Start a fresh session |
| `/help` | Show command list |

### How It Works (Teams)

- Each Teams user gets their own session state (history, selected skills, model)
- If the agent replies within ~4 seconds, the response is sent directly
- If it takes longer, you get a "⏳ Thinking..." message and the real reply is delivered asynchronously via the Bot Framework REST API
- Replies are truncated to ~4000 characters to keep messages readable

### Troubleshooting (Teams)

| Problem | Fix |
|---|---|
| Bot doesn't respond | Check ngrok is running and the URL in Azure matches. Check Flask logs for `[Teams] ✓ /teams endpoint registered` |
| `400 Bad Request` on token | You're using the Secret **ID** instead of the Secret **Value** — create a new secret and copy the Value |
| "You do not have permission" | Your org has sideloading disabled — contact IT or use the Azure "Open in Teams" link instead |
| Replies are slow / "⏳ Thinking..." | Expected for long agent calls. Teams requires a 5s response, so the bot sends a placeholder and follows up |
| ngrok URL changed | Update the messaging endpoint in **Azure Bot → Configuration** and rebuild the zip with the new URL |

---

## WhatsApp Integration (via Twilio)

You can chat with the agent from WhatsApp using the Twilio sandbox — same skills, sessions, all from your phone.

### Step 1 — Create a Free Twilio Account

1. Sign up at [twilio.com](https://www.twilio.com/) (no credit card required for sandbox)
2. From the Twilio Console dashboard, note your **Account SID** and **Auth Token** (under "Account Info")

### Step 2 — Activate the WhatsApp Sandbox

1. In Twilio Console, go to **Messaging → Try it out → Send a WhatsApp message**
2. You'll see a sandbox number (+1 415 523 8886) and a join code (e.g. *"join similar-mostly"*)
3. From your phone, open WhatsApp and send that exact join code to **+1 415 523 8886**
4. Wait for the confirmation reply: *"You are all set!"*

> Each phone number that wants to use the bot must send the join code to opt in.

### Step 3 — Configure the Webhook URL

1. In Twilio Console, go to **Messaging → Try it out → Send a WhatsApp message → Sandbox settings** tab
2. In the **"When a message comes in"** field, enter your ngrok URL with the `/whatsapp` path:
   ```
   https://your-domain.ngrok-free.dev/whatsapp
   ```
3. Set Method to **POST**
4. Leave "Status callback URL" empty
5. Click **Save**

> If you skip this step, Twilio replies with a default "You said: ..." echo message instead of routing to your agent.

### Step 4 — Create `twilio_config.py`

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

### Step 5 — Install Dependencies & Restart

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
| `/mcps` | List available MCP servers |
| `/agents` | List available custom agents |
| `/models` | List available models |
| `/model <id>` | Switch to a specific model (e.g., `/model claude-sonnet-4`) |
| `/use #code-review #testing` | Select skills for your session |
| `/use %workiq` | Select MCP servers for your session |
| `/use @web-search` | Select custom agents for your session |
| `/config` | Show current session config |
| `/sessions` | List recent local Copilot sessions |
| `/resume <id>` | Resume a past session |
| `/new` | Start a fresh session |
| `/help` | Show command list |

> **Tip**: You can mix prefixes in a single command: `/use #code-review %workiq @web-search`

### How It Works (WhatsApp)

- Each phone number gets its own session state (history, selected skills)
- If the agent replies within ~12 seconds, the response is returned inline
- If it takes longer, you get a "⏳ Thinking..." message and the real reply is delivered asynchronously via the Twilio REST API
- Replies are truncated to ~1500 characters to stay within WhatsApp limits

---

## ngrok Tips

- **Custom subdomain** (paid plans): `ngrok http 5000 --subdomain=my-pilot` gives you a stable URL
- **Auth protection**: `ngrok http 5000 --basic-auth="user:password"` adds HTTP basic auth
- **Inspect traffic**: visit `http://127.0.0.1:4040` while ngrok is running to see all requests/responses
- The UI sends `ngrok-skip-browser-warning: 1` header automatically so you won't see the ngrok interstitial page
