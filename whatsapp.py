"""
whatsapp.py — WhatsApp integration via Twilio webhooks.

Registers a /whatsapp endpoint on the Flask app that receives incoming
WhatsApp messages from Twilio, routes them through the existing agent
pipeline, and replies back.

Commands:
  /agents              — list available agents
  /skills              — list available skills
  /use @slug @slug     — select agents for your session
  /use #slug #slug     — select skills for your session
  /config              — show current session config
  /sessions            — list recent local Copilot sessions
  /resume <id>         — resume a local Copilot session
  /new                 — start a fresh session
  /help                — show this help
  (anything else)      — sent as a chat message to the agent
"""

import threading
from flask import request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient

from agent import ask_agent, load_agents, list_skill_directories
from local_sessions import list_local_sessions, get_session_messages, fetch_sessions_sync

# ── Per-phone session state ────────────────────────────────────────────────────
# Keyed by sender phone number (e.g. "whatsapp:+1234567890")
_wa_sessions: dict[str, dict] = {}


def _get_wa_session(sender: str) -> dict:
    """Get or create a WhatsApp session state for a phone number."""
    if sender not in _wa_sessions:
        _wa_sessions[sender] = {
            "history": [],
            "agents": [],
            "skills": [],
            "resumed_session_id": None,
        }
    return _wa_sessions[sender]


def _truncate(text: str, limit: int = 1500) -> str:
    """Truncate text to fit WhatsApp message limits with room to spare."""
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."


# ── Command handlers ───────────────────────────────────────────────────────────

def _handle_help() -> str:
    return (
        "🤖 *Agent Chat — WhatsApp Commands*\n\n"
        "💬 Just type naturally to chat with the agent.\n\n"
        "*/agents* — list available agents\n"
        "*/skills* — list available skills\n"
        "*/use @arch @debug* — select agents\n"
        "*/use #code-review* — select skills\n"
        "*/config* — show current session config\n"
        "*/sessions* — list local Copilot sessions\n"
        "*/resume <id>* — resume a session\n"
        "*/new* — start a fresh session\n"
        "*/help* — show this message"
    )


def _handle_agents() -> str:
    agents = load_agents()
    if not agents:
        return "No agents available."
    lines = ["*Available Agents:*\n"]
    for a in agents:
        lines.append(f"  @{a['slug']} — {a.get('description', a.get('display_name', ''))}")
    lines.append("\nUse */use @slug* to activate one or more.")
    return "\n".join(lines)


def _handle_skills() -> str:
    skills = list_skill_directories()
    if not skills:
        return "No skills available."
    lines = ["*Available Skills:*\n"]
    for s in skills:
        lines.append(f"  #{s['slug']} — {s.get('description', s.get('name', ''))}")
    lines.append("\nUse */use #slug* to activate one or more.")
    return "\n".join(lines)


def _handle_use(args: str, session: dict) -> str:
    """Parse /use @agent1 @agent2 #skill1 #skill2 and update session."""
    tokens = args.split()
    new_agents = [t[1:] for t in tokens if t.startswith("@")]
    new_skills = [t[1:] for t in tokens if t.startswith("#")]

    # Validate agent slugs
    valid_agents = {a["slug"] for a in load_agents()}
    valid_skills = {s["slug"] for s in list_skill_directories()}

    bad = []
    for slug in new_agents:
        if slug not in valid_agents:
            bad.append(f"@{slug}")
    for slug in new_skills:
        if slug not in valid_skills:
            bad.append(f"#{slug}")

    if bad:
        return f"❌ Unknown: {', '.join(bad)}\nUse */agents* or */skills* to see what's available."

    if new_agents:
        session["agents"] = new_agents
    if new_skills:
        session["skills"] = new_skills

    if not new_agents and not new_skills:
        return "Usage: */use @agent-slug #skill-slug*\nExample: */use @architect #code-review*"

    parts = []
    if session["agents"]:
        parts.append("Agents: " + ", ".join(f"@{s}" for s in session["agents"]))
    if session["skills"]:
        parts.append("Skills: " + ", ".join(f"#{s}" for s in session["skills"]))
    return "✅ Session updated.\n" + "\n".join(parts)


def _handle_config(session: dict) -> str:
    agents_str = ", ".join(f"@{s}" for s in session["agents"]) if session["agents"] else "none"
    skills_str = ", ".join(f"#{s}" for s in session["skills"]) if session["skills"] else "none"
    resumed = session["resumed_session_id"] or "none"
    msg_count = len(session["history"])
    return (
        f"*Current Session Config:*\n\n"
        f"Agents: {agents_str}\n"
        f"Skills: {skills_str}\n"
        f"Resumed from: {resumed}\n"
        f"Messages in history: {msg_count}"
    )


def _handle_new(session: dict) -> str:
    session["history"] = []
    session["agents"] = []
    session["skills"] = []
    session["resumed_session_id"] = None
    return "🆕 Session reset. You're starting fresh.\nUse */use* to set agents/skills, or just start chatting."


def _handle_sessions() -> str:
    try:
        # Trigger a fresh fetch first
        fetch_sessions_sync(20)
    except Exception:
        pass

    sessions = list_local_sessions()
    if not sessions:
        return "No local sessions found. Make sure the Copilot CLI has session history."

    lines = ["*Recent Copilot Sessions:*\n"]
    for s in sessions[:10]:  # show top 10
        sid = s.get("sessionId", "?")
        summary = s.get("summary", "Untitled")
        time_str = s.get("startTimeLocal", "")
        short_id = sid[:12]
        lines.append(f"  `{short_id}` — {summary}")
        if time_str:
            lines[-1] += f" ({time_str})"
    lines.append(f"\nUse */resume <id>* to continue a session.")
    return "\n".join(lines)


def _handle_resume(args: str, session: dict) -> str:
    session_id = args.strip()
    if not session_id:
        return "Usage: */resume <session-id>*\nUse */sessions* to find IDs."

    # Try to find a matching session (allow partial ID match)
    all_sessions = list_local_sessions()
    match = None
    for s in all_sessions:
        sid = s.get("sessionId", "")
        if sid == session_id or sid.startswith(session_id):
            match = sid
            break

    if not match:
        return f"❌ No session found matching `{session_id}`.\nUse */sessions* to see available sessions."

    # Load the session messages as initial history
    detail = get_session_messages(match)
    if detail and detail.get("messages"):
        session["history"] = [
            {"role": m["role"], "text": m["text"]}
            for m in detail["messages"]
        ]
    session["resumed_session_id"] = match
    summary = detail.get("summary", "session") if detail else "session"
    return f"📂 Resumed: *{summary}*\nHistory loaded ({len(session['history'])} messages). Send a message to continue."


def _handle_chat(message: str, session: dict, twilio_client, twilio_from: str, sender: str) -> str:
    """
    Send the message to the agent. If it's fast enough, return inline.
    Otherwise, reply with 'thinking...' and send the real reply async.
    """
    history = list(session["history"])  # copy

    # Add user message to history
    session["history"].append({"role": "user", "text": message})

    # We use a threading approach: try the agent call in a thread,
    # if it finishes within ~12 seconds, return inline. Otherwise
    # return a "thinking" message and send the reply asynchronously.

    result_holder = {"reply": None, "error": None}

    def _call_agent():
        try:
            reply = ask_agent(
                message, history,
                resumed_session_id=session.get("resumed_session_id"),
                agent_slugs=session.get("agents", []),
                skill_slugs=session.get("skills", []),
            )
            result_holder["reply"] = reply
        except Exception as e:
            result_holder["error"] = str(e)

    t = threading.Thread(target=_call_agent)
    t.start()
    t.join(timeout=12)  # Twilio allows ~15s; leave 3s buffer

    if result_holder["reply"] is not None:
        # Agent finished in time — return inline
        reply = result_holder["reply"]
        session["history"].append({"role": "agent", "text": reply})
        return _truncate(reply)

    if result_holder["error"] is not None:
        return f"❌ Agent error: {result_holder['error']}"

    # Agent still running — send a placeholder now, deliver the real reply async
    def _send_async():
        t.join()  # wait for agent to finish
        if result_holder["reply"]:
            reply = result_holder["reply"]
            session["history"].append({"role": "agent", "text": reply})
        elif result_holder["error"]:
            reply = f"❌ Agent error: {result_holder['error']}"
        else:
            reply = "❌ Agent timed out. Try a simpler question."

        # Send via Twilio REST API
        try:
            twilio_client.messages.create(
                body=_truncate(reply),
                from_=twilio_from,
                to=sender,
            )
        except Exception as e:
            print(f"[WhatsApp] Failed to send async reply: {e}")

    threading.Thread(target=_send_async, daemon=True).start()
    return "⏳ Thinking... I'll send the full reply in a moment."


# ── Flask registration ─────────────────────────────────────────────────────────

def register_whatsapp_routes(app):
    """Register the /whatsapp webhook route on the given Flask app."""

    # Load Twilio config
    try:
        from twilio_config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM
    except ImportError:
        print("[WhatsApp] ⚠ twilio_config.py not found — WhatsApp endpoint disabled.")
        print("[WhatsApp]   Create twilio_config.py with TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM")
        return

    if "PASTE_YOUR" in TWILIO_ACCOUNT_SID or "PASTE_YOUR" in TWILIO_AUTH_TOKEN:
        print("[WhatsApp] ⚠ twilio_config.py has placeholder values — WhatsApp endpoint disabled.")
        print("[WhatsApp]   Fill in your real Twilio credentials to enable WhatsApp.")
        return

    twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    print(f"[WhatsApp] ✓ Twilio configured — from {TWILIO_WHATSAPP_FROM}")

    @app.route("/whatsapp", methods=["POST"])
    def whatsapp_webhook():
        """Twilio sends incoming WhatsApp messages here."""
        sender = request.form.get("From", "")         # e.g. whatsapp:+1234567890
        body   = request.form.get("Body", "").strip()

        if not body:
            resp = MessagingResponse()
            resp.message("Send a message or type */help* for commands.")
            return str(resp), 200, {"Content-Type": "text/xml"}

        session = _get_wa_session(sender)
        text = body.strip()

        # Route commands
        if text.lower() == "/help":
            reply = _handle_help()
        elif text.lower() == "/agents":
            reply = _handle_agents()
        elif text.lower() == "/skills":
            reply = _handle_skills()
        elif text.lower().startswith("/use "):
            reply = _handle_use(text[5:], session)
        elif text.lower() == "/use":
            reply = _handle_use("", session)
        elif text.lower() == "/config":
            reply = _handle_config(session)
        elif text.lower() == "/new":
            reply = _handle_new(session)
        elif text.lower() == "/sessions":
            reply = _handle_sessions()
        elif text.lower().startswith("/resume "):
            reply = _handle_resume(text[8:], session)
        elif text.lower() == "/resume":
            reply = _handle_resume("", session)
        else:
            # Regular chat message
            reply = _handle_chat(
                text, session,
                twilio_client, TWILIO_WHATSAPP_FROM, sender,
            )

        resp = MessagingResponse()
        resp.message(reply)
        return str(resp), 200, {"Content-Type": "text/xml"}

    print("[WhatsApp] ✓ /whatsapp endpoint registered")
