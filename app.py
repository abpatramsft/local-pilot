"""
app.py — Local agent server for agent-chat.html
Run: python app.py
"""
import os
import json
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from agent import ask_agent, ask_agent_streaming

app = Flask(__name__)
CORS(app)  # required so the HTML file (file://) can call localhost


@app.route("/health", methods=["GET"])
def health():
    """Used by the chat UI to show the live indicator dot."""
    return jsonify({"status": "ok"})


@app.route("/chat", methods=["POST"])
def chat():
    data    = request.json or {}
    message = data.get("message", "").strip()
    history = data.get("history", [])   # list of {role, text} dicts

    if not message:
        return jsonify({"error": "empty message"}), 400

    try:
        reply = ask_agent(message, history)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/chat/stream", methods=["POST"])
def chat_stream():
    """Server-Sent Events endpoint for streaming responses."""
    data    = request.json or {}
    message = data.get("message", "").strip()
    history = data.get("history", [])

    if not message:
        return jsonify({"error": "empty message"}), 400

    def generate():
        try:
            for event in ask_agent_streaming(message, history):
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
