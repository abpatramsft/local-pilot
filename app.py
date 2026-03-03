"""
app.py — Local agent server for agent-chat.html
Run: python app.py
"""
import os
import json
import threading
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from agent import ask_agent, ask_agent_streaming, list_skill_directories, list_mcp_servers, list_custom_agents
from local_sessions import list_local_sessions, get_session_messages, fetch_sessions_sync
from whatsapp import register_whatsapp_routes

app = Flask(__name__)
CORS(app)  # required so the HTML file (file://) can call localhost

# Register WhatsApp webhook (only activates if twilio_config.py is filled in)
register_whatsapp_routes(app)


@app.route("/health", methods=["GET"])
def health():
    """Used by the chat UI to show the live indicator dot."""
    return jsonify({"status": "ok"})


# ── Local Session Endpoints ───────────────────────────────────────────────────

@app.route("/local-sessions", methods=["GET"])
def local_sessions_list():
    """Return list of already-fetched local Copilot sessions with summaries."""
    try:
        sessions = list_local_sessions()
        return jsonify({"sessions": sessions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/local-sessions/fetch", methods=["POST"])
def local_sessions_fetch():
    """Trigger a fresh fetch of sessions from the Copilot CLI."""
    data = request.json or {}
    limit = data.get("limit", 50)
    try:
        result = fetch_sessions_sync(limit)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/local-sessions/<session_id>", methods=["GET"])
def local_session_detail(session_id):
    """Return the full conversation for a local session, parsed into messages."""
    try:
        session = get_session_messages(session_id)
        if session is None:
            return jsonify({"error": "Session not found"}), 404
        return jsonify(session)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Skill, MCP & Agent Endpoints ──────────────────────────────────────────────────────────────

@app.route("/skills", methods=["GET"])
def list_skills_endpoint():
    """Return list of available skills from the skills/ directory."""
    try:
        skills = list_skill_directories()
        return jsonify({"skills": skills})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/mcps", methods=["GET"])
def list_mcps_endpoint():
    """Return list of available MCP servers from mcp.json."""
    try:
        mcps = list_mcp_servers()
        return jsonify({"mcps": mcps})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/agents", methods=["GET"])
def list_agents_endpoint():
    """Return list of available custom agents from agents.json."""
    try:
        agents = list_custom_agents()
        return jsonify({"agents": agents})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/chat", methods=["POST"])
def chat():
    data    = request.json or {}
    message = data.get("message", "").strip()
    history = data.get("history", [])   # list of {role, text} dicts
    resumed_session_id = data.get("resumed_session_id")
    skill_slugs = data.get("skills", [])
    mcp_slugs = data.get("mcps", [])
    agent_slugs = data.get("agents", [])
    ui_session_id = data.get("ui_session_id")

    if not message:
        return jsonify({"error": "empty message"}), 400

    try:
        reply = ask_agent(
            message, history,
            resumed_session_id=resumed_session_id,
            skill_slugs=skill_slugs,
            ui_session_id=ui_session_id,
            mcp_slugs=mcp_slugs,
            agent_slugs=agent_slugs,
        )
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    """Server-Sent Events endpoint for streaming responses."""
    data    = request.json or {}
    message = data.get("message", "").strip()
    history = data.get("history", [])
    resumed_session_id = data.get("resumed_session_id")
    skill_slugs = data.get("skills", [])
    mcp_slugs = data.get("mcps", [])
    agent_slugs = data.get("agents", [])
    ui_session_id = data.get("ui_session_id")

    if not message:
        return jsonify({"error": "empty message"}), 400

    def generate():
        try:
            for event in ask_agent_streaming(
                message, history,
                resumed_session_id=resumed_session_id,
                skill_slugs=skill_slugs,
                ui_session_id=ui_session_id,
                mcp_slugs=mcp_slugs,
                agent_slugs=agent_slugs,
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  Agent chat server running → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=True)
