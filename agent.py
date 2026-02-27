"""
agent.py — Wrap the GitHub Copilot SDK here
Uses the GitHub Copilot SDK to power the chat agent.
"""
import asyncio
import threading
import hashlib
import os
import json
import queue
from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType

# Persistent event loop running in a background thread
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_client: CopilotClient | None = None
_sessions: dict[str, object] = {}  # session cache keyed by conversation hash
_lock = threading.Lock()

# Working directory for file operations - defaults to pilot_folder subdirectory
_DEFAULT_WORKSPACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pilot_folder")
WORKSPACE_DIR = os.environ.get("COPILOT_WORKSPACE", _DEFAULT_WORKSPACE)

# Ensure the workspace folder exists
os.makedirs(WORKSPACE_DIR, exist_ok=True)

# System message that instructs the agent about workspace behavior
SYSTEM_MESSAGE = f"""You are a helpful coding assistant.

Default workspace: {WORKSPACE_DIR}

IMPORTANT: By default, ALL file operations (create, read, write, delete, list) MUST happen inside the workspace folder above. Always use absolute paths starting with {WORKSPACE_DIR} when creating or accessing files. If the user explicitly asks to work in a different folder, use that folder instead.
"""


def _start_background_loop():
    """Start and run the event loop in a background thread."""
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Ensure the background event loop is running."""
    global _loop, _loop_thread
    with _lock:
        if _loop is None or not _loop.is_running():
            _loop_thread = threading.Thread(target=_start_background_loop, daemon=True)
            _loop_thread.start()
            # Wait for loop to start
            while _loop is None or not _loop.is_running():
                pass
    return _loop


def _get_conversation_key(history: list) -> str:
    """Generate a stable key for a conversation based on its history."""
    if not history:
        return "default"
    # Use first message as key - this identifies the conversation
    first_msg = history[0].get("text", "") if history else ""
    return hashlib.md5(first_msg.encode()).hexdigest()[:16]


async def _ensure_client() -> CopilotClient:
    """Initialize the Copilot client if not already started."""
    global _client
    if _client is None:
        _client = CopilotClient({"cwd": WORKSPACE_DIR})
        await _client.start()
    return _client


async def _get_or_create_session(client: CopilotClient, conversation_key: str):
    """Get existing session or create a new one for this conversation."""
    global _sessions
    if conversation_key not in _sessions:
        session = await client.create_session({
            "model": "gpt-4.1",
            "streaming": True,
            "system_message": {
                "content": SYSTEM_MESSAGE,
            },
        })
        _sessions[conversation_key] = session
    return _sessions[conversation_key]


async def _ask_agent_streaming_async(message: str, history: list, event_queue: queue.Queue):
    """
    Async implementation that streams events via a queue.
    
    Args:
        message : the latest user message
        history : list of previous turns
        event_queue : queue to push events to
    """
    client = await _ensure_client()
    conversation_key = _get_conversation_key(history)
    session = await _get_or_create_session(client, conversation_key)
    
    content_parts = []
    
    def handle_event(event):
        if event.type == SessionEventType.ASSISTANT_MESSAGE_DELTA:
            delta = event.data.delta_content
            content_parts.append(delta)
            event_queue.put({
                "type": "delta",
                "content": delta
            })
        
        elif event.type == SessionEventType.TOOL_EXECUTION_START:
            tool_name = event.data.tool_name if hasattr(event.data, 'tool_name') else 'unknown'
            args = event.data.arguments if hasattr(event.data, 'arguments') else None
            event_queue.put({
                "type": "tool_start",
                "tool": tool_name,
                "args": args
            })
        
        elif event.type == SessionEventType.TOOL_EXECUTION_COMPLETE:
            tool_name = event.data.tool_name if hasattr(event.data, 'tool_name') else 'unknown'
            event_queue.put({
                "type": "tool_complete",
                "tool": tool_name
            })
        
        elif event.type == SessionEventType.SESSION_ERROR:
            error_msg = event.data.message if hasattr(event.data, 'message') else 'Unknown error'
            event_queue.put({
                "type": "error",
                "message": error_msg
            })
        
        elif event.type == SessionEventType.SESSION_IDLE:
            event_queue.put({
                "type": "done",
                "content": "".join(content_parts)
            })
    
    session.on(handle_event)
    await session.send_and_wait({"prompt": message})


async def _ask_agent_async(message: str, history: list) -> str:
    """
    Async implementation that uses the Copilot SDK.
    
    Args:
        message : the latest user message
        history : list of previous turns [{"role": "user"|"agent", "text": "..."}]

    Returns:
        Agent's reply as a string
    """
    client = await _ensure_client()
    
    # Get or create a session for this conversation
    conversation_key = _get_conversation_key(history)
    session = await _get_or_create_session(client, conversation_key)
    
    # Send the current message - session automatically maintains context
    response = await session.send_and_wait({"prompt": message})
    
    return response.data.content if response and response.data else ""


def ask_agent_streaming(message: str, history: list):
    """
    Generator that yields streaming events from the agent.
    
    Yields:
        dict with event type and data
    """
    event_queue = queue.Queue()
    loop = _ensure_loop()
    
    # Start the async task
    future = asyncio.run_coroutine_threadsafe(
        _ask_agent_streaming_async(message, history, event_queue),
        loop
    )
    
    # Yield events as they come in
    while True:
        try:
            event = event_queue.get(timeout=0.1)
            yield event
            if event.get("type") == "done":
                break
        except queue.Empty:
            # Check if the future is done (with error)
            if future.done():
                try:
                    future.result()  # This will raise if there was an exception
                except Exception as e:
                    yield {"type": "error", "message": str(e)}
                break


def ask_agent(message: str, history: list) -> str:
    """
    Synchronous wrapper for the async Copilot SDK call.
    
    Args:
        message : the latest user message
        history : list of previous turns [{"role": "user"|"agent", "text": "..."}]

    Returns:
        Agent's reply as a string
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(_ask_agent_async(message, history), loop)
    return future.result(timeout=120)  # 2 minute timeout
