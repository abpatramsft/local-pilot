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


def _approve_all_permissions(request, context):
    """Auto-approve every tool permission request (shell, write, read, url, mcp)."""
    return {"kind": "approved"}

# Persistent event loop running in a background thread
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_client: CopilotClient | None = None
_sessions: dict[str, object] = {}  # session cache keyed by conversation hash
_resumed_sdk_sessions: dict[str, object] = {}  # sessions resumed via client.resume_session()
_copilot_id_to_session: dict[str, object] = {}  # copilot session_id → SDK session (covers both created & resumed)
_active_unsubscribers: dict[int, callable] = {}  # id(session) → unsubscribe fn, prevents duplicate handlers
_session_config_cache: dict[str, tuple] = {}  # key → (sorted skill_slugs, sorted mcp_slugs) last used
_lock = threading.Lock()

# Working directory for file operations - defaults to pilot_folder subdirectory
_DEFAULT_WORKSPACE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pilot_folder")
WORKSPACE_DIR = os.environ.get("COPILOT_WORKSPACE", _DEFAULT_WORKSPACE)

# Ensure the workspace folder exists
os.makedirs(WORKSPACE_DIR, exist_ok=True)

# Directories for skills, MCP config, and custom agents config
SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
MCP_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp.json")
AGENTS_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents.json")

# System message that instructs the agent about workspace behavior
SYSTEM_MESSAGE = f"""You are a helpful coding assistant.

Default workspace: {WORKSPACE_DIR}

IMPORTANT: By default, ALL file operations (create, read, write, delete, list) MUST happen inside the workspace folder above. Always use absolute paths starting with {WORKSPACE_DIR} when creating or accessing files. If the user explicitly asks to work in a different folder, use that folder instead.
"""


def load_mcp_servers() -> dict:
    """Load MCP server configurations from mcp.json."""
    if not os.path.isfile(MCP_CONFIG_FILE):
        return {}
    try:
        with open(MCP_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("servers", {})
    except (json.JSONDecodeError, IOError):
        return {}


def list_mcp_servers() -> list[dict]:
    """Return a list of available MCP servers with their metadata."""
    servers = load_mcp_servers()
    result = []
    for slug, cfg in servers.items():
        result.append({
            "slug": cfg.get("slug", slug),
            "name": cfg.get("name", slug),
            "description": cfg.get("description", ""),
        })
    return result


def load_custom_agents() -> dict:
    """Load custom agent configurations from agents.json."""
    if not os.path.isfile(AGENTS_CONFIG_FILE):
        return {}
    try:
        with open(AGENTS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("agents", {})
    except (json.JSONDecodeError, IOError):
        return {}


def list_custom_agents() -> list[dict]:
    """Return a list of available custom agents with their metadata."""
    agents = load_custom_agents()
    result = []
    for slug, cfg in agents.items():
        result.append({
            "slug": slug,
            "name": cfg.get("display_name", cfg.get("name", slug)),
            "description": cfg.get("description", ""),
        })
    return result


def list_skill_directories() -> list[dict]:
    """List available skills from the skills/ directory."""
    skills = []
    if not os.path.isdir(SKILLS_DIR):
        return skills
    for dirname in sorted(os.listdir(SKILLS_DIR)):
        skill_dir = os.path.join(SKILLS_DIR, dirname)
        skill_file = os.path.join(skill_dir, "SKILL.md")
        if os.path.isdir(skill_dir) and os.path.isfile(skill_file):
            name = dirname
            description = ""
            try:
                with open(skill_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                if content.startswith('---'):
                    parts = content.split('---', 2)
                    if len(parts) >= 3:
                        for line in parts[1].strip().split('\n'):
                            if line.startswith('name:'):
                                name = line.split(':', 1)[1].strip()
                            elif line.startswith('description:'):
                                description = line.split(':', 1)[1].strip()
            except IOError:
                pass
            skills.append({
                "slug": dirname,
                "name": name,
                "description": description,
                "directory": skill_dir,
            })
    return skills


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


def _build_session_config(
    skill_slugs: list[str] | None = None,
    mcp_slugs: list[str] | None = None,
    agent_slugs: list[str] | None = None,
    is_new: bool = True,
) -> dict:
    """Build session config dict for create_session or resume_session.

    Args:
        is_new: True for create_session (includes model / system_message),
                False for resume_session (server already knows those).
    """
    config: dict = {
        "streaming": True,
        "on_permission_request": _approve_all_permissions,
    }
    if is_new:
        config["model"] = "gpt-4.1"
        config["system_message"] = {"content": SYSTEM_MESSAGE}

    # Skill directories
    if skill_slugs:
        all_skills = list_skill_directories()
        all_skill_slugs = [s["slug"] for s in all_skills]
        config["skill_directories"] = [SKILLS_DIR]
        disabled = [s for s in all_skill_slugs if s not in skill_slugs]
        if disabled:
            config["disabled_skills"] = disabled

    # MCP servers (from explicit mcp_slugs + any agent-level MCP servers)
    mcp_servers = {}
    if mcp_slugs:
        all_mcps = load_mcp_servers()
        for slug in mcp_slugs:
            if slug in all_mcps:
                cfg = all_mcps[slug]
                mcp_servers[slug] = {
                    "type": cfg.get("type", "local"),
                    "command": cfg["command"],
                    "args": cfg.get("args", []),
                    "tools": cfg.get("tools", ["*"]),
                }

    # Custom agents
    if agent_slugs:
        all_agents = load_custom_agents()
        custom_agents = []
        for slug in agent_slugs:
            if slug in all_agents:
                acfg = all_agents[slug]
                agent_def: dict = {
                    "name": acfg.get("name", slug),
                    "prompt": acfg.get("prompt", ""),
                }
                if "display_name" in acfg:
                    agent_def["display_name"] = acfg["display_name"]
                if "description" in acfg:
                    agent_def["description"] = acfg["description"]
                if "tools" in acfg:
                    agent_def["tools"] = acfg["tools"]
                if "infer" in acfg:
                    agent_def["infer"] = acfg["infer"]
                # Agent-level MCP servers get merged into session-level MCPs
                if "mcp_servers" in acfg:
                    for mcp_name, mcp_cfg in acfg["mcp_servers"].items():
                        mcp_servers[mcp_name] = mcp_cfg
                custom_agents.append(agent_def)
        if custom_agents:
            config["custom_agents"] = custom_agents

    if mcp_servers:
        config["mcp_servers"] = mcp_servers

    return config


def _config_fingerprint(
    skill_slugs: list[str] | None = None,
    mcp_slugs: list[str] | None = None,
    agent_slugs: list[str] | None = None,
) -> tuple:
    """Return a hashable fingerprint of the current skill/MCP/agent config."""
    return (
        tuple(sorted(skill_slugs)) if skill_slugs else (),
        tuple(sorted(mcp_slugs)) if mcp_slugs else (),
        tuple(sorted(agent_slugs)) if agent_slugs else (),
    )


async def _destroy_old_session(old_session) -> None:
    """Soft-destroy an old SDK session before re-resuming.

    This tells the server to release its observer for this session so that
    the subsequent resume_session() won't create a second observer (which
    would cause every streamed token to be delivered twice).
    Conversation history is preserved server-side — only the live connection
    is torn down.
    """
    # Clean up any lingering event handler we may still hold
    old_sid = id(old_session)
    stale = _active_unsubscribers.pop(old_sid, None)
    if stale:
        try:
            stale()
        except Exception:
            pass
    try:
        await old_session.destroy()
    except Exception:
        pass  # best-effort; resume_session will reconnect regardless


async def _get_or_create_session(
    client: CopilotClient, conversation_key: str,
    skill_slugs: list[str] | None = None,
    mcp_slugs: list[str] | None = None,
    agent_slugs: list[str] | None = None,
):
    """Get existing session or create/re-resume one.

    If skills, MCPs, or agents changed since the last call, the old session is
    destroyed (soft disconnect) and re-resumed with the new config.
    Conversation history is preserved server-side.
    """
    new_fp = _config_fingerprint(skill_slugs, mcp_slugs, agent_slugs)

    if conversation_key in _sessions:
        old_fp = _session_config_cache.get(conversation_key)
        if old_fp == new_fp:
            return _sessions[conversation_key]  # config unchanged — reuse

        # Config changed — destroy old observer, then re-resume
        old_session = _sessions[conversation_key]
        copilot_sid = old_session.session_id if hasattr(old_session, 'session_id') else None
        if copilot_sid:
            await _destroy_old_session(old_session)
            config = _build_session_config(skill_slugs, mcp_slugs, agent_slugs, is_new=False)
            session = await client.resume_session(copilot_sid, config)
            _sessions[conversation_key] = session
            _copilot_id_to_session[copilot_sid] = session
            _session_config_cache[conversation_key] = new_fp
            return session

    # First call — create a brand-new session
    config = _build_session_config(skill_slugs, mcp_slugs, agent_slugs, is_new=True)
    session = await client.create_session(config)
    _sessions[conversation_key] = session
    _session_config_cache[conversation_key] = new_fp
    # Track by Copilot session ID so resume_session can find it later
    if hasattr(session, 'session_id') and session.session_id:
        _copilot_id_to_session[session.session_id] = session
    return session


async def _get_or_resume_session(
    client: CopilotClient, session_id: str,
    skill_slugs: list[str] | None = None,
    mcp_slugs: list[str] | None = None,
    agent_slugs: list[str] | None = None,
):
    """Resume a local Copilot CLI session by ID.

    If skills, MCPs, or agents changed since the last call, the session is
    re-resumed with the new config while preserving conversation history (server-side).
    """
    new_fp = _config_fingerprint(skill_slugs, mcp_slugs, agent_slugs)

    if session_id in _resumed_sdk_sessions:
        old_fp = _session_config_cache.get(f"resume:{session_id}")
        if old_fp == new_fp:
            return _resumed_sdk_sessions[session_id]  # config unchanged — reuse

        # Config changed — destroy old observer, then re-resume
        await _destroy_old_session(_resumed_sdk_sessions[session_id])
        config = _build_session_config(skill_slugs, mcp_slugs, agent_slugs, is_new=False)
        session = await client.resume_session(session_id, config)
        _resumed_sdk_sessions[session_id] = session
        _copilot_id_to_session[session_id] = session
        _session_config_cache[f"resume:{session_id}"] = new_fp
        return session

    # Check if this Copilot session was already created via create_session —
    # reuse it to avoid two SDK objects connected to the same server session
    # (which causes duplicated streaming output).
    if session_id in _copilot_id_to_session:
        old_fp = _session_config_cache.get(f"resume:{session_id}")
        if old_fp == new_fp:
            session = _copilot_id_to_session[session_id]
            _resumed_sdk_sessions[session_id] = session
            return session

        # Config changed vs what was used at creation — destroy old, re-resume
        await _destroy_old_session(_copilot_id_to_session[session_id])
        config = _build_session_config(skill_slugs, mcp_slugs, agent_slugs, is_new=False)
        session = await client.resume_session(session_id, config)
        _resumed_sdk_sessions[session_id] = session
        _copilot_id_to_session[session_id] = session
        _session_config_cache[f"resume:{session_id}"] = new_fp
        return session

    # First resume — no existing session found
    config = _build_session_config(skill_slugs, mcp_slugs, agent_slugs, is_new=False)
    session = await client.resume_session(session_id, config)
    _resumed_sdk_sessions[session_id] = session
    _copilot_id_to_session[session_id] = session
    _session_config_cache[f"resume:{session_id}"] = new_fp
    return session


async def _ask_agent_streaming_async(
    message: str, history: list, event_queue: queue.Queue,
    resumed_session_id: str | None = None,
    skill_slugs: list[str] | None = None,
    ui_session_id: str | None = None,
    mcp_slugs: list[str] | None = None,
    agent_slugs: list[str] | None = None,
):
    """
    Async implementation that streams events via a queue.
    
    Args:
        message : the latest user message
        history : list of previous turns
        event_queue : queue to push events to
        resumed_session_id : if set, resume this Copilot CLI session via the SDK
        skill_slugs : list of skill slugs to load
        ui_session_id : UI session ID for stable session caching
        mcp_slugs : list of MCP server slugs to connect
        agent_slugs : list of custom agent slugs to activate
    """
    client = await _ensure_client()
    if resumed_session_id:
        session = await _get_or_resume_session(client, resumed_session_id, skill_slugs, mcp_slugs, agent_slugs)
    else:
        conversation_key = ui_session_id or _get_conversation_key(history)
        session = await _get_or_create_session(client, conversation_key, skill_slugs, mcp_slugs, agent_slugs)
    
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
                "content": "".join(content_parts),
                "copilot_session_id": session.session_id if hasattr(session, 'session_id') else None
            })
    
    # Defensively remove any lingering handler from a previous call on this
    # session — guards against the race where unsubscribe() in the finally block
    # hasn't executed yet when the next message arrives (causes doubled tokens).
    sid = id(session)
    stale = _active_unsubscribers.pop(sid, None)
    if stale:
        try:
            stale()
        except Exception:
            pass

    unsubscribe = session.on(handle_event)
    _active_unsubscribers[sid] = unsubscribe
    try:
        await session.send_and_wait({"prompt": message}, 600000)  # 10 min timeout (ms)
    finally:
        unsubscribe()
        _active_unsubscribers.pop(sid, None)


async def _ask_agent_async(
    message: str, history: list, resumed_session_id: str | None = None,
    skill_slugs: list[str] | None = None,
    ui_session_id: str | None = None,
    mcp_slugs: list[str] | None = None,
    agent_slugs: list[str] | None = None,
) -> str:
    """
    Async implementation that uses the Copilot SDK.
    
    Args:
        message : the latest user message
        history : list of previous turns [{"role": "user"|"agent", "text": "..."}]
        resumed_session_id : if set, resume this Copilot CLI session via the SDK
        skill_slugs : list of skill slugs to load
        ui_session_id : UI session ID for stable session caching
        mcp_slugs : list of MCP server slugs to connect
        agent_slugs : list of custom agent slugs to activate

    Returns:
        Agent's reply as a string
    """
    client = await _ensure_client()
    
    if resumed_session_id:
        session = await _get_or_resume_session(client, resumed_session_id, skill_slugs, mcp_slugs, agent_slugs)
    else:
        conversation_key = ui_session_id or _get_conversation_key(history)
        session = await _get_or_create_session(client, conversation_key, skill_slugs, mcp_slugs, agent_slugs)
    
    # Send the current message - session automatically maintains context
    response = await session.send_and_wait({"prompt": message}, 600000)  # 10 min timeout (ms)
    
    return response.data.content if response and response.data else ""


def ask_agent_streaming(
    message: str, history: list, resumed_session_id: str | None = None,
    skill_slugs: list[str] | None = None,
    ui_session_id: str | None = None,
    mcp_slugs: list[str] | None = None,
    agent_slugs: list[str] | None = None,
):
    """
    Generator that yields streaming events from the agent.
    
    Yields:
        dict with event type and data
    """
    event_queue = queue.Queue()
    loop = _ensure_loop()
    
    # Start the async task
    future = asyncio.run_coroutine_threadsafe(
        _ask_agent_streaming_async(
            message, history, event_queue, resumed_session_id,
            skill_slugs, ui_session_id, mcp_slugs, agent_slugs,
        ),
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


def ask_agent(
    message: str, history: list, resumed_session_id: str | None = None,
    skill_slugs: list[str] | None = None,
    ui_session_id: str | None = None,
    mcp_slugs: list[str] | None = None,
    agent_slugs: list[str] | None = None,
) -> str:
    """
    Synchronous wrapper for the async Copilot SDK call.
    
    Args:
        message : the latest user message
        history : list of previous turns [{"role": "user"|"agent", "text": "..."}]
        resumed_session_id : if set, resume this Copilot CLI session via the SDK
        skill_slugs : list of skill slugs to load
        ui_session_id : UI session ID for stable session caching
        mcp_slugs : list of MCP server slugs to connect
        agent_slugs : list of custom agent slugs to activate

    Returns:
        Agent's reply as a string
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(
        _ask_agent_async(
            message, history, resumed_session_id,
            skill_slugs, ui_session_id, mcp_slugs, agent_slugs,
        ), loop
    )
    return future.result(timeout=300)  # 5 minute timeout for long-running tasks
