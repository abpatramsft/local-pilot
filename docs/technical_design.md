# Technical Design Document — Local Pilot

> **Version**: 1.0  
> **Last updated**: March 2026  

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Component Details](#3-component-details)
   - [3.1 Flask Server (app.py)](#31-flask-server-apppy)
   - [3.2 Agent Core (agent.py)](#32-agent-core-agentpy)
   - [3.3 Local Sessions (local_sessions.py)](#33-local-sessions-local_sessionspy)
   - [3.4 Chat UI (index.html)](#34-chat-ui-indexhtml)
   - [3.5 Teams Integration (teams.py)](#35-teams-integration-teamspy)
   - [3.6 WhatsApp Integration (whatsapp.py)](#36-whatsapp-integration-whatsapppy)
   - [3.7 Teams App Package (teams-app/)](#37-teams-app-package-teams-app)
4. [Data Models & Schemas](#4-data-models--schemas)
   - [4.1 Session State (In-Memory)](#41-session-state-in-memory)
   - [4.2 Skill Descriptor](#42-skill-descriptor)
   - [4.3 MCP Server Config (mcp.json)](#43-mcp-server-config-mcpjson)
   - [4.4 Custom Agent Config (agents.json)](#44-custom-agent-config-agentsjson)
   - [4.5 Models Config (models_config.json)](#45-models-config-models_configjson)
   - [4.6 Chat Message](#46-chat-message)
   - [4.7 SSE Event Types](#47-sse-event-types)
5. [Session Management](#5-session-management)
   - [5.1 Session Lifecycle](#51-session-lifecycle)
   - [5.2 Session Cache Architecture](#52-session-cache-architecture)
   - [5.3 Config Fingerprinting & Hot-Swap](#53-config-fingerprinting--hot-swap)
   - [5.4 Session Resumption](#54-session-resumption)
6. [API Surface](#6-api-surface)
7. [Integration Architecture](#7-integration-architecture)
   - [7.1 Teams Bot Framework Flow](#71-teams-bot-framework-flow)
   - [7.2 WhatsApp Twilio Flow](#72-whatsapp-twilio-flow)
   - [7.3 Async Reply Pattern](#73-async-reply-pattern)
8. [Streaming Architecture](#8-streaming-architecture)
9. [Skill System](#9-skill-system)
10. [MCP Server System](#10-mcp-server-system)
11. [Custom Agent System](#11-custom-agent-system)
12. [Threading & Concurrency Model](#12-threading--concurrency-model)
13. [Security Considerations](#13-security-considerations)
14. [Configuration Reference](#14-configuration-reference)
15. [File Map](#15-file-map)

---

## 1. System Overview

Local Pilot is a self-hosted agent chat application that wraps the **GitHub Copilot SDK** behind a Flask server. It exposes the agent through multiple channels:

- **Web UI** — a single-file HTML chat interface (accessed locally or via ngrok)
- **Microsoft Teams** — a personal bot via Azure Bot Framework
- **WhatsApp** — via Twilio sandbox webhooks

All channels share the same agent backend, skills, MCP servers, custom agents, and model selection.

### Design Goals

| Goal | Approach |
|---|---|
| **Remote access** | ngrok tunnel exposes localhost to the public internet |
| **Multi-channel** | Single agent core serves Web, Teams, and WhatsApp |
| **Extensible** | Skills (markdown), MCP servers (JSON config), custom agents (JSON config) |
| **Stateful** | In-memory session caching with conversation-key routing |
| **No build step** | Web UI is a single HTML file, server is a single `python app.py` |
| **Sandboxed** | File operations scoped to `pilot_folder/` by default |

---

## 2. High-Level Architecture

```
                          ┌─────────────────────────────────────────┐
                          │              External Clients             │
                          │                                           │
                          │  ┌──────────┐  ┌───────┐  ┌───────────┐ │
                          │  │ Browser/ │  │ Teams │  │ WhatsApp  │ │
                          │  │ Mobile   │  │       │  │ (Twilio)  │ │
                          │  └────┬─────┘  └───┬───┘  └─────┬─────┘ │
                          └───────┼────────────┼─────────────┼───────┘
                                  │            │             │
                              HTTPS        HTTPS          HTTPS
                           (ngrok)     (Bot Framework)  (Twilio webhook)
                                  │            │             │
                          ┌───────▼────────────▼─────────────▼───────┐
                          │              ngrok tunnel                  │
                          │   (public URL → localhost:5000)           │
                          └───────────────────┬───────────────────────┘
                                              │
                          ┌───────────────────▼───────────────────────┐
                          │           Flask Server (app.py)            │
                          │                                            │
                          │  ┌──────────┐ ┌──────────┐ ┌───────────┐ │
                          │  │ /chat    │ │ /teams   │ │ /whatsapp │ │
                          │  │ /chat/   │ │          │ │           │ │
                          │  │  stream  │ │          │ │           │ │
                          │  └────┬─────┘ └────┬─────┘ └─────┬─────┘ │
                          │       │            │             │        │
                          │  ┌────▼────────────▼─────────────▼─────┐ │
                          │  │          Agent Core (agent.py)       │ │
                          │  │                                      │ │
                          │  │  ┌───────────┐  ┌────────────────┐  │ │
                          │  │  │ CopilotSDK│  │ Session Cache  │  │ │
                          │  │  │  Client   │  │ (in-memory)    │  │ │
                          │  │  └─────┬─────┘  └────────────────┘  │ │
                          │  └────────┼────────────────────────────┘ │
                          └───────────┼────────────────────────────────┘
                                      │
                          ┌───────────▼───────────────────────────────┐
                          │        GitHub Copilot Backend              │
                          │  (model inference, tool execution,         │
                          │   session persistence)                     │
                          └────────────────────────────────────────────┘
```

---

## 3. Component Details

### 3.1 Flask Server (`app.py`)

The entry point. Registers all HTTP routes and wires together the subsystems.

**Responsibilities:**
- Serves REST + SSE endpoints for the web UI (`/chat`, `/chat/stream`, `/health`, `/skills`, `/mcps`, `/agents`, `/models`)
- Serves local session endpoints (`/local-sessions`, `/local-sessions/fetch`, `/local-sessions/<id>`)
- Delegates to `register_whatsapp_routes()` and `register_teams_routes()` for integration webhooks
- Enables CORS so the `file://` or remote-hosted UI can call the server
- Runs on `0.0.0.0:5000` (configurable via `PORT` env var)

**Key design decisions:**
- Non-streaming `/chat` returns a JSON `{ "reply": "..." }`
- Streaming `/chat/stream` returns Server-Sent Events with typed JSON payloads
- Integration modules are imported unconditionally but gracefully degrade if config files are missing

### 3.2 Agent Core (`agent.py`)

The heart of the system — manages a persistent connection to the Copilot SDK.

**Responsibilities:**
- Initialises and maintains a single `CopilotClient` instance
- Manages a persistent asyncio event loop on a background thread
- Caches SDK sessions keyed by conversation identity
- Builds session configs (skills, MCP servers, agents, model) and detects config changes
- Provides both synchronous (`ask_agent`) and streaming (`ask_agent_streaming`) entry points
- Loads configuration from `mcp.json`, `agents.json`, `models_config.json`, and `skills/`
- Auto-approves all tool permission requests (shell, write, read, URL, MCP)

**Key internal state:**

| Variable | Type | Purpose |
|---|---|---|
| `_loop` | `asyncio.AbstractEventLoop` | Persistent background event loop |
| `_client` | `CopilotClient` | Single SDK client instance |
| `_sessions` | `dict[str, Session]` | Sessions keyed by conversation hash |
| `_resumed_sdk_sessions` | `dict[str, Session]` | Sessions resumed by Copilot session ID |
| `_copilot_id_to_session` | `dict[str, Session]` | Maps Copilot session ID → SDK session object |
| `_active_unsubscribers` | `dict[int, callable]` | Tracks event handlers to prevent duplicate streaming |
| `_session_config_cache` | `dict[str, tuple]` | Last-used config fingerprint per session key |

### 3.3 Local Sessions (`local_sessions.py`)

Fetches and browses past Copilot CLI sessions entirely in-memory.

**Responsibilities:**
- Connects to Copilot CLI via SDK RPCs (`session.list`, `session.getMessages`)
- Stores session metadata and events in-memory (no disk I/O)
- Parses raw events into `{role, text}` message lists
- Provides `fetch_sessions_sync()` (triggers fresh fetch), `list_local_sessions()` (returns cached index), `get_session_messages(id)` (returns parsed conversation)

**In-memory store:**

| Variable | Type | Purpose |
|---|---|---|
| `_session_index` | `list[dict]` | Session metadata from `session.list` |
| `_session_events` | `dict[str, dict]` | Full session data + events, keyed by session ID |

### 3.4 Chat UI (`index.html`)

A self-contained, dark-themed, mobile-responsive chat interface. No build step.

**Features:**
- SSE streaming with real-time token rendering
- Tool call visibility (start/complete/error)
- Reasoning/thinking display in collapsible blocks (for models like Claude Sonnet 4)
- Sidebar with skills, MCP, agents, and model dropdowns
- Session browser for local Copilot CLI sessions
- ngrok endpoint configuration in the sidebar footer
- Sends `ngrok-skip-browser-warning: 1` header to bypass ngrok interstitial

### 3.5 Teams Integration (`teams.py`)

Registers a `/teams` webhook endpoint for the Azure Bot Framework.

**Responsibilities:**
- Receives incoming Teams activities (messages) via POST
- Maintains per-user session state keyed by Teams user ID
- Routes slash commands (`/skills`, `/use`, `/model`, etc.) to handlers
- Delegates chat messages to `ask_agent()` with a 4-second timeout
- Sends async follow-up replies via Bot Framework REST API when the agent takes longer
- Authenticates using OAuth 2.0 client credentials flow against the Microsoft identity platform
- Token is cached with TTL-based refresh

**Graceful degradation:** If `teams_config.py` is missing or contains placeholders, the endpoint is silently disabled with a console warning.

### 3.6 WhatsApp Integration (`whatsapp.py`)

Registers a `/whatsapp` webhook endpoint for Twilio.

**Responsibilities:**
- Receives incoming WhatsApp messages via Twilio's POST webhook
- Maintains per-phone-number session state
- Routes slash commands identically to Teams
- Delegates chat messages to `ask_agent()` with a 12-second timeout
- Sends async follow-up replies via Twilio REST API when the agent takes longer
- Returns TwiML responses for inline replies

**Graceful degradation:** If `twilio_config.py` is missing or contains placeholders, the endpoint is silently disabled.

### 3.7 Teams App Package (`teams-app/`)

A build script that generates a sideloadable Teams app `.zip`.

**Contents:**
- `manifest.json` — Teams app manifest template with placeholder values
- `generate_teams_app.py` — CLI script that fills in credentials, generates bot icons (via Pillow), and packages into `local-pilot.zip`

---

## 4. Data Models & Schemas

### 4.1 Session State (In-Memory)

Used by both Teams and WhatsApp integrations to track per-user state:

```python
{
    "history": [                        # list of conversation turns
        {"role": "user", "text": "..."},
        {"role": "agent", "text": "..."}
    ],
    "skills": ["code-review"],          # active skill slugs
    "mcps": ["workiq"],                 # active MCP server slugs
    "agents": ["web-search"],           # active custom agent slugs
    "model": "gpt-4.1",                # current model ID (None = use default)
    "resumed_session_id": "abc123..."   # Copilot session ID if resumed (None otherwise)
}
```

**Storage:**
- Teams: `_teams_sessions: dict[str, dict]` — keyed by Teams user ID (`activity["from"]["id"]`)
- WhatsApp: `_wa_sessions: dict[str, dict]` — keyed by sender phone (`"whatsapp:+1234567890"`)
- Web UI: session state lives client-side; the server uses conversation-key hashing or `ui_session_id`

### 4.2 Skill Descriptor

Skills are directories under `skills/` containing a `SKILL.md` file with YAML frontmatter:

```yaml
---
name: Code Review
description: Provides structured code review with quality scoring
---

# Code Review Skill
(markdown instructions for the agent)
```

**Parsed into:**

```python
{
    "slug": "code-review",       # directory name
    "name": "Code Review",       # from frontmatter
    "description": "Provides structured code review...",
    "directory": "/path/to/skills/code-review"
}
```

**How skills are loaded:** The `skill_directories` config points to the `skills/` folder. All skills in the directory are enabled by default; unwanted skills are passed via `disabled_skills` to exclude them.

### 4.3 MCP Server Config (`mcp.json`)

```json
{
  "servers": {
    "workiq": {
      "slug": "workiq",
      "name": "Work IQ",
      "description": "Query Microsoft 365 data...",
      "type": "local",
      "command": "npx",
      "args": ["-y", "@microsoft/workiq", "mcp"],
      "tools": ["*"]
    }
  }
}
```

Each server entry is passed to the Copilot SDK as an `mcp_servers` config when creating or resuming a session.

### 4.4 Custom Agent Config (`agents.json`)

```json
{
  "agents": {
    "web-search": {
      "name": "web-search",
      "display_name": "Web Search Agent",
      "description": "Searches the web...",
      "prompt": "You are a web research specialist...",
      "infer": true
    },
    "work-iq": {
      "name": "work-iq",
      "display_name": "Work IQ Agent",
      "description": "Queries Microsoft 365 data...",
      "prompt": "You are a Microsoft 365 productivity assistant...",
      "mcp_servers": {
        "workiq": { "type": "local", "command": "npx", "args": [...], "tools": ["*"] }
      },
      "infer": true
    }
  }
}
```

**Agent fields:**

| Field | Type | Description |
|---|---|---|
| `name` | string | Internal identifier |
| `display_name` | string | Shown in the UI |
| `description` | string | Short explainer text |
| `prompt` | string | System-level prompt defining persona |
| `tools` | list[str] | Built-in tool names to enable |
| `mcp_servers` | dict | Embedded MCP server configs activated with this agent |
| `infer` | bool | Whether the agent should infer tool usage |

**Important:** When a custom agent has `mcp_servers`, those are merged into session-level MCP servers at config build time.

### 4.5 Models Config (`models_config.json`)

```json
{
  "default_model": "gpt-4.1",
  "models": [
    { "id": "gpt-4.1",        "name": "GPT-4.1",        "description": "..." },
    { "id": "claude-sonnet-4", "name": "Claude Sonnet 4", "description": "..." },
    { "id": "gpt-5-mini",     "name": "GPT-5 Mini",     "description": "..." }
  ]
}
```

The selected model is part of the session config fingerprint — switching models triggers a session re-resume.

### 4.6 Chat Message

The universal message format used across the system:

```python
{"role": "user" | "agent", "text": "message content"}
```

### 4.7 SSE Event Types

Events streamed over the `/chat/stream` endpoint:

| Event Type | Fields | Description |
|---|---|---|
| `delta` | `content` | Incremental text token from the agent |
| `reasoning_delta` | `content` | Thinking/reasoning token (for models with CoT) |
| `tool_start` | `tool`, `args` | A tool execution has begun |
| `tool_complete` | `tool` | A tool execution completed |
| `error` | `message` | An error occurred |
| `done` | `content`, `copilot_session_id` | Stream finished, includes full text and session ID |

---

## 5. Session Management

### 5.1 Session Lifecycle

```
┌──────────────────┐
│ New conversation  │
│ (first message)   │
└────────┬─────────┘
         │
         ▼
┌────────────────────────┐
│ Generate conversation  │
│ key (MD5 of first msg  │
│ or ui_session_id)      │
└────────┬───────────────┘
         │
         ▼
┌────────────────────────┐     ┌──────────────────────┐
│ Key exists in cache?   │─Yes─│ Check config finger- │
│                        │     │ print changed?        │
└────────┬───────────────┘     └──────────┬───────────┘
         │ No                             │
         ▼                       ┌────────┴────────┐
┌─────────────────────┐          │ Yes              │ No
│ create_session()    │          ▼                  ▼
│ with full config    │  ┌──────────────┐  ┌──────────────┐
│ (model, system msg, │  │ destroy()    │  │ Return cached │
│  skills, MCPs,      │  │ old session  │  │ session       │
│  agents)            │  │ → resume()   │  └──────────────┘
└─────────────────────┘  │ with new cfg │
                         └──────────────┘
```

### 5.2 Session Cache Architecture

The agent core maintains several caches to avoid creating duplicate SDK sessions:

```
  UI / Integration                  Agent Core                     Copilot Backend
  ─────────────                     ──────────                     ───────────────
                              ┌──────────────────┐
  conversation_key ──────────►│   _sessions{}    │──── SDK session ────► server session
  (hash or ui_id)             └──────────────────┘
                              ┌──────────────────┐
  copilot_session_id ────────►│ _resumed_sdk_    │──── SDK session ────► server session
  (from CLI / resume)         │   sessions{}     │
                              └──────────────────┘
                              ┌──────────────────┐
  copilot_session_id ────────►│ _copilot_id_to_  │ (de-dupe: prevents two SDK objects
                              │   session{}      │  for the same server session)
                              └──────────────────┘
```

### 5.3 Config Fingerprinting & Hot-Swap

Every time a message is sent, the system computes a **config fingerprint**:

```python
fingerprint = (
    sorted(skill_slugs),
    sorted(mcp_slugs),
    sorted(agent_slugs),
    model_id
)
```

If the fingerprint differs from the cached one for that session:
1. The old session's event observer is destroyed (prevents duplicate streaming)
2. A new `resume_session()` is issued with the updated config
3. Conversation history is preserved (it's stored server-side)
4. The new session object replaces the old one in all caches

This enables **mid-conversation model switching** and **skill/MCP hot-swapping** without losing context.

### 5.4 Session Resumption

There are two paths to resume a session:

1. **By conversation key** — the same UI session continues. The SDK session is reused or re-resumed if config changed.
2. **By Copilot session ID** — resumes a historical CLI session. The system checks `_copilot_id_to_session` first to avoid creating duplicate SDK connections to the same server session.

**Destroy-before-resume:** Before re-resuming with new config, the old session is explicitly destroyed to release the server-side observer. This prevents every streamed token from being delivered twice.

---

## 6. API Surface

### REST Endpoints

| Method | Path | Request Body | Response | Description |
|---|---|---|---|---|
| `GET` | `/health` | — | `{"status": "ok"}` | Health check for UI connectivity indicator |
| `POST` | `/chat` | JSON (see below) | `{"reply": "..."}` | Non-streaming agent call |
| `POST` | `/chat/stream` | JSON (see below) | SSE stream | Streaming agent call |
| `GET` | `/skills` | — | `{"skills": [...]}` | List available skills |
| `GET` | `/mcps` | — | `{"mcps": [...]}` | List available MCP servers |
| `GET` | `/agents` | — | `{"agents": [...]}` | List available custom agents |
| `GET` | `/models` | — | `{"models": [...], "default_model": "..."}` | List models + default |
| `GET` | `/local-sessions` | — | `{"sessions": [...]}` | List cached local sessions |
| `POST` | `/local-sessions/fetch` | `{"limit": 50}` | `{"total_found", "fetched", "status"}` | Trigger fresh session fetch |
| `GET` | `/local-sessions/<id>` | — | `{session detail}` | Full conversation for a session |
| `POST` | `/teams` | Bot Framework activity JSON | `{}` | Teams bot webhook |
| `POST` | `/whatsapp` | Twilio form data | TwiML XML | WhatsApp webhook |

### Chat Request Body

```json
{
  "message": "Hello",
  "history": [{"role": "user", "text": "..."}, {"role": "agent", "text": "..."}],
  "resumed_session_id": "optional-copilot-session-id",
  "skills": ["code-review"],
  "mcps": ["workiq"],
  "agents": ["web-search"],
  "model": "gpt-4.1",
  "ui_session_id": "optional-stable-session-key"
}
```

---

## 7. Integration Architecture

### 7.1 Teams Bot Framework Flow

```
User in Teams
     │
     │ sends message
     ▼
Azure Bot Service
     │
     │ POST activity JSON
     ▼
ngrok → Flask /teams
     │
     ├─ Parse activity, extract user_id + text
     ├─ Get/create per-user session state
     ├─ Route: slash command? → handler → reply
     │         regular text?  → _handle_chat()
     │
     └─ _handle_chat():
          ├─ Start ask_agent() in a thread
          ├─ Wait 4 seconds (join timeout)
          │
          ├─ Agent finished? → return reply directly
          │
          └─ Still running?
               ├─ Return "⏳ Thinking..." immediately
               └─ Spawn daemon thread:
                    ├─ Wait for agent to finish
                    └─ POST reply via Bot Framework REST API
                         (OAuth token → /v3/conversations/{id}/activities/{id})
```

**Authentication:** The bot authenticates using OAuth 2.0 client credentials:
- Token endpoint: `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token`
- Scope: `https://api.botframework.com/.default`
- Tokens are cached with expiry-based refresh (1-minute buffer)

### 7.2 WhatsApp Twilio Flow

```
User in WhatsApp
     │
     │ sends message
     ▼
Twilio Platform
     │
     │ POST form data (From, Body, ...)
     ▼
ngrok → Flask /whatsapp
     │
     ├─ Parse form data, extract sender + body
     ├─ Get/create per-phone session state
     ├─ Route: slash command? → handler → reply
     │         regular text?  → _handle_chat()
     │
     └─ _handle_chat():
          ├─ Start ask_agent() in a thread
          ├─ Wait 12 seconds (join timeout)
          │
          ├─ Agent finished? → return reply inline (TwiML)
          │
          └─ Still running?
               ├─ Return "⏳ Thinking..." as TwiML
               └─ Spawn daemon thread:
                    ├─ Wait for agent to finish
                    └─ Send reply via twilio_client.messages.create()
```

### 7.3 Async Reply Pattern

Both Teams and WhatsApp share the same timeout-based async pattern:

| Platform | Inline Timeout | Why | Async Delivery |
|---|---|---|---|
| Teams | 4 seconds | Bot Framework requires response in ~5s | Bot Framework REST API (`POST /v3/conversations/.../activities/...`) |
| WhatsApp | 12 seconds | Twilio allows ~15s | Twilio REST API (`client.messages.create()`) |

The pattern:
1. Start agent call in a background thread
2. `thread.join(timeout=N)` — wait up to N seconds
3. If done: return result inline
4. If still running: return placeholder, spawn a daemon thread that waits for completion and delivers the real reply via the platform's REST API

---

## 8. Streaming Architecture

The streaming pipeline for the web UI:

```
Browser                           Flask                          Agent Core                      Copilot SDK
───────                           ─────                          ──────────                      ───────────
POST /chat/stream ───────────►  generate() ──────────────►  ask_agent_streaming() ─────►  session.send_and_wait()
                                   │                              │                              │
                                   │                              │  subscribes to session        │
                                   │                              │  events via session.on()      │
                                   │                              │                              │
                                   │         ◄─── queue.get() ─── queue.put() ◄─── handle_event()
                                   │                              │                              │
                              SSE: data: {...}\n\n                 │  events:                      │
                                   │                              │  - ASSISTANT_MESSAGE_DELTA    │
                                   │                              │  - ASSISTANT_REASONING_DELTA  │
                                   │                              │  - TOOL_EXECUTION_START       │
                                   │                              │  - TOOL_EXECUTION_COMPLETE    │
                                   │                              │  - SESSION_ERROR              │
                                   │                              │  - SESSION_IDLE (→ "done")    │
                                   │                              │                              │
                              SSE: data: {"type":"done"}\n\n      unsubscribe()                  │
```

**Key details:**
- Events flow: SDK → `handle_event()` callback → `queue.Queue` → Flask generator → SSE
- The `_active_unsubscribers` dict prevents duplicate event handlers when a new message arrives before the previous handler is fully cleaned up
- Timeout: 10 minutes (600,000ms) per `send_and_wait()` call

---

## 9. Skill System

```
skills/
├── code-review/
│   └── SKILL.md          ← YAML frontmatter + markdown instructions
├── docs-writer/
│   └── SKILL.md
├── security-audit/
│   └── SKILL.md
└── testing/
    └── SKILL.md
```

**Loading flow:**
1. `list_skill_directories()` scans `skills/`, parses YAML frontmatter from each `SKILL.md`
2. When a session is created/resumed:
   - `skill_directories` is set to `[SKILLS_DIR]` (the whole folder)
   - `disabled_skills` lists all skill slugs NOT in the user's selected set
3. The Copilot SDK reads and injects skill instructions into the agent's context

**Skill selection:**
- Web UI: dropdown in sidebar
- Teams/WhatsApp: `/use #code-review #testing`

---

## 10. MCP Server System

MCP (Model Context Protocol) servers provide external tools the agent can invoke.

**Loading flow:**
1. `load_mcp_servers()` reads `mcp.json` → `servers` dict
2. When a session is created with selected MCP slugs, each matching server's config is added to the session's `mcp_servers`
3. Agent-level MCP servers (from `agents.json`) are merged in at the same step
4. The Copilot SDK spawns the MCP process and makes its tools available to the agent

**Config structure per server:**

```python
{
    "type": "local",          # server type
    "command": "npx",         # executable
    "args": ["-y", "@microsoft/workiq", "mcp"],  # arguments
    "tools": ["*"]            # which tools to expose ("*" = all)
}
```

---

## 11. Custom Agent System

Custom agents define specialised personas with dedicated prompts and tools.

**How they integrate into sessions:**
1. `load_custom_agents()` reads `agents.json` → `agents` dict
2. When selected, each agent is converted to a `custom_agents` entry in the session config:
   ```python
   {
       "name": "web-search",
       "display_name": "Web Search Agent",
       "prompt": "You are a web research specialist...",
       "description": "...",
       "tools": [...],
       "infer": true
   }
   ```
3. Agent-level `mcp_servers` are extracted and merged into session-level `mcp_servers`
4. The Copilot SDK activates the agent's persona and tools within the session

---

## 12. Threading & Concurrency Model

```
Main Thread                    Background Event Loop Thread        Per-Request Threads
───────────                    ────────────────────────────        ───────────────────
Flask WSGI server              asyncio event loop                  (Teams/WhatsApp only)
  │                              │
  ├─ /chat (sync)                │
  │    └─ run_coroutine_         │
  │       threadsafe() ──────────┤ _ask_agent_async()
  │       future.result(300s)    │   └─ session.send_and_wait()
  │                              │
  ├─ /chat/stream (generator)    │
  │    └─ run_coroutine_         │
  │       threadsafe() ──────────┤ _ask_agent_streaming_async()
  │    queue.get(timeout=0.1)    │   └─ session.on(handle_event)
  │    yield SSE                 │       queue.put(event)
  │                              │
  ├─ /teams (sync)               │
  │    └─ _handle_chat()         │
  │        ├─ Thread(_call_agent)│
  │        │   └─ ask_agent() ───┤ (uses run_coroutine_threadsafe)
  │        ├─ join(timeout=4)    │
  │        └─ Thread(_send_async)│  (daemon, if timed out)
  │                              │
  └─ /whatsapp (sync)            │
       └─ _handle_chat()         │
           ├─ Thread(_call_agent)│
           │   └─ ask_agent() ───┤
           ├─ join(timeout=12)   │
           └─ Thread(_send_async)│  (daemon, if timed out)
```

**Key points:**
- A single persistent `asyncio` event loop runs in a daemon thread (`_start_background_loop`)
- All Copilot SDK calls are dispatched to this loop via `asyncio.run_coroutine_threadsafe()`
- Flask runs in the main thread with its default WSGI server
- Teams/WhatsApp handlers spawn per-request threads for the agent call + async delivery
- A `threading.Lock` (`_lock`) protects event loop initialisation
- `_fetch_lock` serialises local session fetches

---

## 13. Security Considerations

| Area | Implementation |
|---|---|
| **Credential management** | `teams_config.py` and `twilio_config.py` are `.gitignore`d; never committed |
| **Token handling** | GitHub token sourced from env vars (`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`); falls back to `gh auth` |
| **Bot Framework auth** | OAuth 2.0 client credentials; tokens cached with expiry |
| **File sandboxing** | All file operations default to `pilot_folder/`; system message instructs the agent to stay within this directory |
| **Tool permissions** | Auto-approved via `_approve_all_permissions()` — all tool types (shell, write, read, URL, MCP) are allowed |
| **CORS** | Fully open (`flask-cors` with defaults) to support `file://` and cross-origin UI hosting |
| **ngrok** | Recommended to use auth protection (`--basic-auth`) for production-like usage |

> **Note:** The auto-approve-all permission policy is a convenience trade-off. For production deployments, implement a selective approval callback.

---

## 14. Configuration Reference

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5000` | Flask server port |
| `COPILOT_WORKSPACE` | `./pilot_folder` | Working directory for file operations |
| `COPILOT_GITHUB_TOKEN` | — | Primary GitHub token |
| `GH_TOKEN` | — | Fallback GitHub token |
| `GITHUB_TOKEN` | — | Second fallback GitHub token |

### Config Files

| File | Purpose | Committed? |
|---|---|---|
| `mcp.json` | MCP server definitions | Yes |
| `agents.json` | Custom agent personas | Yes |
| `models_config.json` | Available models & default | Yes |
| `teams_config.py` | Azure Bot credentials | **No** (`.gitignore`) |
| `twilio_config.py` | Twilio credentials | **No** (`.gitignore`) |

### Directories

| Directory | Purpose |
|---|---|
| `skills/` | Skill definitions (each subdirectory has a `SKILL.md`) |
| `pilot_folder/` | Sandboxed workspace for agent file operations |
| `teams-app/` | Teams app manifest + package builder |

---

## 15. File Map

```
local-pilot/
│
├── app.py                    # Flask server — route registration, REST + SSE endpoints
├── agent.py                  # Copilot SDK wrapper — session management, streaming, config
├── local_sessions.py         # Fetch & browse past Copilot CLI sessions (in-memory)
├── index.html                # Self-contained chat UI (HTML + CSS + JS)
│
├── teams.py                  # Teams integration — Bot Framework webhook + command routing
├── teams_config.py           # Azure Bot credentials (⚠ not committed)
├── teams-app/
│   ├── manifest.json         # Teams app manifest template
│   └── generate_teams_app.py # Script to build sideloadable .zip
│
├── whatsapp.py               # WhatsApp integration — Twilio webhook + command routing
├── twilio_config.py          # Twilio credentials (⚠ not committed)
│
├── mcp.json                  # MCP server configurations
├── agents.json               # Custom agent definitions
├── models_config.json        # Available models + default
├── requirements.txt          # Python dependencies
│
├── skills/                   # Agent skill definitions
│   ├── code-review/SKILL.md
│   ├── docs-writer/SKILL.md
│   ├── security-audit/SKILL.md
│   └── testing/SKILL.md
│
├── pilot_folder/             # Sandboxed workspace for file operations
│   ├── bmi-tracker.html
│   ├── calorie-tracker.html
│   └── documents/
│       └── todo.md
│
└── docs/                     # Documentation
    ├── integration_setup_guide.md   # Teams + WhatsApp setup instructions
    └── technical_design.md          # This document
```
