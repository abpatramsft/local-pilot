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
- **MCP Servers** — connect external tool servers via `mcp.json` (e.g., Work IQ, database tools) — the agent can invoke their tools during conversations
- **Custom Agents** — define specialised agent personas in `agents.json` with custom prompts, dedicated tools, and embedded MCP servers (e.g., a web-search agent, a work-iq agent)
- **Model switching** — swap the underlying Copilot model at any time (during a chat, when resuming, or when starting fresh) via a dropdown in the UI or `/model` commands on WhatsApp. Configure available models in `models_config.json`
- **Reasoning / thinking display** — models that emit thinking tokens (e.g., Claude Sonnet 4) get a collapsible "Thinking…" block rendered in the chat so you can inspect the chain of thought
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
├── teams.py               # Teams integration via Azure Bot Framework
├── teams_config.py        # Azure Bot credentials (⚠ do not commit)
├── teams-app/             # Teams app package builder
│   ├── manifest.json      # App manifest template (placeholder values)
│   └── generate_teams_app.py  # Script to build the installable .zip
├── index.html             # Self-contained chat UI (HTML + CSS + JS)
├── requirements.txt       # Python dependencies
├── mcp.json               # MCP server configurations
├── agents.json            # Custom agent definitions (prompts, tools, MCPs)
├── models_config.json     # Available models and default model selection
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
| `GET` | `/mcps` | List available MCP servers |
| `GET` | `/agents` | List available custom agents |
| `GET` | `/models` | List available models and the default model |
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

## MCP Servers

Configure external tool servers in `mcp.json`:

```json
{
  "workiq": {
    "command": "npx",
    "args": ["-y", "@microsoft/workiq", "mcp"]
  }
}
```

Each key becomes a selectable MCP server in the **⚡ MCPs** dropdown. The agent can invoke tools provided by these servers during conversations.

## Custom Agents

Define specialised agent personas in `agents.json`:

```json
{
  "web-search": {
    "name": "Web Search",
    "description": "Agent with web browsing capabilities",
    "prompt": "You are a research assistant with web access...",
    "tools": ["web_fetch"]
  },
  "work-iq": {
    "name": "Work IQ",
    "description": "Agent powered by Microsoft Work IQ",
    "prompt": "You are an intelligent work assistant...",
    "mcp_servers": {
      "workiq": {
        "command": "npx",
        "args": ["-y", "@microsoft/workiq", "mcp"]
      }
    }
  }
}
```

Each key becomes a selectable agent in the **🤖 Agents** dropdown. Agents can have:

| Field | Description |
|---|---|
| `name` | Display name in the UI |
| `description` | Short description shown in the dropdown |
| `prompt` | System prompt that defines the agent's persona and behaviour |
| `tools` | List of built-in tool names the agent should use (e.g., `web_fetch`) |
| `mcp_servers` | Embedded MCP server configs that are activated when this agent is selected |

## Models

Configure the models available for selection in `models_config.json`:

```json
{
  "default_model": "gpt-4.1",
  "models": [
    {
      "id": "gpt-4.1",
      "name": "GPT-4.1",
      "description": "Fast, cost-efficient flagship model for most coding tasks"
    },
    {
      "id": "claude-sonnet-4",
      "name": "Claude Sonnet 4",
      "description": "Anthropic's balanced model with extended thinking"
    },
    {
      "id": "gpt-5-mini",
      "name": "GPT-5 Mini",
      "description": "Compact next-gen model with strong reasoning"
    }
  ]
}
```

Models appear in the **🧠 Model** dropdown in the UI. The selected model is part of the session config fingerprint — switching models mid-conversation triggers a session re-resume so the new model takes effect immediately while preserving conversation history.

Models that emit reasoning / thinking tokens (e.g., Claude Sonnet 4) will have their chain-of-thought rendered in a collapsible "Thinking…" block in the chat UI.

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `PORT` | `5000` | Flask server port |
| `COPILOT_WORKSPACE` | `./pilot_folder` | Working directory for file operations |
| `COPILOT_GITHUB_TOKEN` | — | GitHub token for Copilot SDK |
| `GH_TOKEN` | — | Fallback GitHub token |
| `GITHUB_TOKEN` | — | Second fallback GitHub token |

## Integrations (Teams, WhatsApp)

Local-pilot can also be used as a **Microsoft Teams** personal bot or via **WhatsApp** (Twilio sandbox). Both integrations share the same agent, skills, sessions, and commands.

For full setup instructions, see **[docs/integration_setup_guide.md](docs/integration_setup_guide.md)**.

For a detailed technical design covering architecture, session management, data models, streaming, and threading, see **[docs/technical_design.md](docs/technical_design.md)**.

## ngrok Tips

- **Custom subdomain** (paid plans): `ngrok http 5000 --subdomain=my-pilot` gives you a stable URL
- **Auth protection**: `ngrok http 5000 --basic-auth="user:password"` adds HTTP basic auth
- **Inspect traffic**: visit `http://127.0.0.1:4040` while ngrok is running to see all requests/responses
- The UI sends `ngrok-skip-browser-warning: 1` header automatically so you won't see the ngrok interstitial page

## License

Private / Internal use.
