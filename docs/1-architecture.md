---
title: "agent-gate - Architecture"
created: 2026-02-07T23:42:07Z
tags: [openclaw, agent-gate, architecture]
generated_by: Claude Code
---

# Architecture

Detailed component designs for agent-gate v1. Complements [0-spec.md](./0-spec.md).

## System Overview

```
                          ┌─────────────────────────────────────────────────────┐
                          │                   agent-gate                        │
                          │                                                     │
Agent ── WebSocket ──────→│  server.py ──→ engine.py ──┬──→ executor.py ──→ HA │
      ←── JSON-RPC ───── │     ↑              │        │        ↑               │
                          │     │              │        │        │               │
                          │  auth check    deny/allow   │   services/           │
                          │  (bearer)         │        ask   homeassistant.py   │
                          │                   │        │                         │
                          │                   │        ↓                         │
                          │                   │   messenger/                     │
                          │                   │   telegram.py ──→ Telegram API  │
                          │                   │        ↑                         │
                          │                   │        │ callback                │
                          │                   │   User taps button              │
                          │                   │        │                         │
                          │                   ↓        ↓                         │
                          │               db.py (SQLite)                        │
                          │               - audit_log                           │
                          │               - pending_requests                    │
                          └─────────────────────────────────────────────────────┘
```

## Component Details

### 1. server.py — WebSocket Server

The entrypoint for all agent communication. Accepts a single WebSocket connection, authenticates it, and dispatches JSON-RPC requests to the permission engine.

**Responsibilities:**
- Listen on `config.gateway.host:config.gateway.port`
- Authenticate agent connection (bearer token via JSON-RPC `auth` method — must be first message within 10s)
- Parse incoming JSON-RPC 2.0 messages
- Dispatch `tool_request` method to `engine.evaluate()`
- Hold a reference to pending requests (awaiting human approval)
- Return results/errors back over WebSocket
- Reject second connections while one agent is connected (v1: single agent)

**Libraries:** `websockets`

**Connection lifecycle:**

```
Agent connects → WSS handshake (TLS)
  → 10-second auth deadline starts
  → Agent sends: {"jsonrpc":"2.0","method":"auth","params":{"token":"..."},"id":"auth-1"}
  → Gateway validates bearer token
  → Gateway responds: {"jsonrpc":"2.0","result":{"status":"authenticated"},"id":"auth-1"}
  → Agent sends tool_request messages (rate-limited)
  → Agent can call get_pending_results after reconnect
  → ...
  → Agent disconnects (pending approvals continue) or gateway shuts down
```

**Pending request handling:**

When the engine returns `ask`, the server:
1. Stores the request in `pending_requests` (in-memory dict + SQLite for persistence)
2. Triggers the messenger adapter to send an approval message
3. Does NOT respond to the agent yet — the WebSocket request stays open
4. When the messenger callback fires (approve/deny/timeout), resolves the pending request
5. Sends the JSON-RPC result or error to the agent

This means tool requests with `ask` policy are **long-lived** — the WebSocket connection stays open and the agent blocks (up to 15 minutes).

**Error codes:**

| Code | Meaning |
|---|---|
| -32700 | Parse error (malformed JSON) |
| -32600 | Invalid request (missing fields) |
| -32601 | Method not found |
| -32001 | Approval denied by user |
| -32002 | Approval timed out |
| -32003 | Policy denied (no human involved) |
| -32004 | Action execution failed (service error) |
| -32005 | Not authenticated |
| -32006 | Rate limit exceeded |

---

### 2. engine.py — Permission Engine

Evaluates tool requests against the permission policy and returns a decision.

**Responsibilities:**
- Load and parse `permissions.yaml`
- Build a tool call signature string: `tool_name(arg1, arg2, ...)`
- Match against rules using `fnmatch`
- Return a decision: `allow`, `deny`, or `ask`

**Evaluation order:**

```python
def evaluate(tool_name: str, args: dict) -> Decision:
    signature = build_signature(tool_name, args)

    # Phase 1: Check explicit rules (sorted by action priority)
    for rule in rules_sorted_by_priority:
        if fnmatch(signature, rule.pattern):
            if rule.action == "deny":
                return Decision.DENY
    for rule in rules_sorted_by_priority:
        if fnmatch(signature, rule.pattern):
            if rule.action == "allow":
                return Decision.ALLOW
    for rule in rules_sorted_by_priority:
        if fnmatch(signature, rule.pattern):
            if rule.action == "ask":
                return Decision.ASK

    # Phase 2: Check defaults
    for pattern, action in defaults.items():
        if fnmatch(signature, pattern):
            return Decision(action)

    # Phase 3: Global fallback
    return Decision.ASK
```

**Signature building:**

Each tool type has an explicit, deterministic signature builder. This avoids dependence on JSON field ordering.

```python
import re

# Strict allowlist for HA identifiers (domain, service, entity_id, event_type)
HA_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)?$")

# Characters forbidden in ANY argument value (security: prevents glob/signature injection)
FORBIDDEN_CHARS_RE = re.compile(r"[*?\[\](),\x00-\x1f]")

SIGNATURE_BUILDERS: dict[str, Callable[[dict], list[str]]] = {
    "ha_call_service": lambda args: [
        f"{args.get('domain', '')}.{args.get('service', '')}",
        args.get("entity_id", ""),
    ],
    "ha_get_state": lambda args: [args.get("entity_id", "")],
    "ha_get_states": lambda args: [],
    "ha_fire_event": lambda args: [args.get("event_type", "")],
}

def validate_args(tool_name: str, args: dict) -> None:
    """Reject args with forbidden characters. Raises ValueError."""
    for key, value in args.items():
        if not isinstance(value, str):
            continue
        if FORBIDDEN_CHARS_RE.search(value):
            raise ValueError(f"Argument '{key}' contains forbidden characters")
        # Extra validation for HA identifiers
        if tool_name.startswith("ha_") and key in ("entity_id", "domain", "service", "event_type"):
            if not HA_IDENTIFIER_RE.match(value):
                raise ValueError(f"Invalid HA identifier format: {key}={value}")

def build_signature(tool_name: str, args: dict) -> str:
    """Build a deterministic, matchable signature string.

    Examples:
        build_signature("ha_get_state", {"entity_id": "sensor.temp"})
        → "ha_get_state(sensor.temp)"

        build_signature("ha_call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.bedroom"})
        → "ha_call_service(light.turn_on, light.bedroom)"
    """
    validate_args(tool_name, args)

    builder = SIGNATURE_BUILDERS.get(tool_name)
    if builder:
        parts = builder(args)
        return f"{tool_name}({', '.join(parts)})" if parts else tool_name

    # Fallback for unknown tools: sorted keys for determinism
    parts = [str(args[k]) for k in sorted(args.keys())]
    return f"{tool_name}({', '.join(parts)})" if parts else tool_name
```

**Security properties:**
- Argument values are validated BEFORE signature building — forbidden characters cause request rejection (error -32600)
- HA identifiers are validated against a strict regex (`^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)?$`)
- Signature part ordering is deterministic per tool type (not dependent on JSON field order)
- Unknown tool names still get deterministic signatures via sorted-key fallback

---

### 3. executor.py — Action Execution Dispatcher

Routes approved tool requests to the appropriate service handler and returns results.

**Responsibilities:**
- Maintain a registry of service handlers
- Dispatch tool requests by tool name prefix (`ha_*` → HomeAssistantService)
- Return structured results or raise execution errors
- Log all executions to the audit log

**Structure:**

```python
# Explicit tool-to-service mapping (not prefix parsing — avoids fragile string splitting)
TOOL_SERVICE_MAP = {
    "ha_get_state": "homeassistant",
    "ha_get_states": "homeassistant",
    "ha_call_service": "homeassistant",
    "ha_fire_event": "homeassistant",
}

class Executor:
    def __init__(self, services: dict[str, ServiceHandler]):
        self.services = services  # {"homeassistant": HomeAssistantService, ...}

    async def execute(self, tool_name: str, args: dict) -> dict:
        service_name = TOOL_SERVICE_MAP.get(tool_name)
        if service_name is None:
            raise ExecutionError(f"Unknown tool: {tool_name}")
        handler = self.services.get(service_name)
        if handler is None:
            raise ExecutionError(f"Service not configured: {service_name}")
        return await handler.execute(tool_name, args)
```

Unknown tool names are rejected at the executor layer (defense in depth — the permission engine may allow a tool, but if it's not in `TOOL_SERVICE_MAP`, execution still fails). v1 only has one service handler (Home Assistant). The `services/base.py` ABC allows adding more in v2+.

---

### 4. services/homeassistant.py — Home Assistant Client

Executes Home Assistant actions via its REST API.

**Responsibilities:**
- Maintain an `aiohttp.ClientSession` with the HA long-lived access token
- Implement the `ServiceHandler` interface
- Map tool names to HA REST API calls

**Tool mapping:**

| Tool name | HA API call | Policy default |
|---|---|---|
| `ha_get_state` | `GET /api/states/{entity_id}` | allow |
| `ha_get_states` | `GET /api/states` | allow |
| `ha_call_service` | `POST /api/services/{domain}/{service}` | ask |
| `ha_fire_event` | `POST /api/events/{event_type}` | deny |

**ServiceHandler ABC:**

```python
from abc import ABC, abstractmethod

class ServiceHandler(ABC):
    """Interface for service integrations."""

    @abstractmethod
    async def execute(self, tool_name: str, args: dict) -> dict:
        """Execute a tool call and return the result."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the service is reachable. Non-blocking."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources (HTTP sessions, etc.)."""
        ...
```

**Health check details:**
- HA: `GET /api/` (returns `{"message": "API running."}`) with 5-second timeout
- Telegram: `bot.get_me()` to verify bot token validity
- Both log warnings on failure but do not prevent startup

**Error mapping for HA calls:**

| HA Response | JSON-RPC Error | Message |
|---|---|---|
| HTTP 401 | -32004 | `"Service authentication failed (HA token expired?)"` |
| HTTP 404 | -32004 | `"Entity not found: {entity_id}"` |
| Connection refused | -32004 | `"Service unreachable: homeassistant"` |
| HTTP 200 + error body | -32004 | HA's error message forwarded |

---

### 5. messenger/telegram.py — Guardian Bot

The Telegram bot that sends approval requests and receives human decisions.

**Responsibilities:**
- Run a polling loop via `python-telegram-bot` (PTB v21)
- Send inline keyboard messages for `ask` decisions
- Handle callback queries (button taps)
- Edit messages after decisions (approved/denied/expired)
- Manage approval timeouts

**PTB configuration:**

```python
from telegram.ext import Application, CallbackQueryHandler, PicklePersistence

persistence = PicklePersistence(filepath="data/callback_data.pickle")

app = (
    Application.builder()
    .token(config.messenger.telegram.token)
    .persistence(persistence)
    .arbitrary_callback_data(True)  # Sidesteps 64-byte callback_data limit
    .build()
)
```

With `arbitrary_callback_data=True`, PTB stores Python objects server-side and substitutes a UUID as the actual `callback_data` sent to Telegram. This means:
- No 64-byte limit on what we store per button
- No need for manual HMAC signing
- The Pi (different bot token) physically cannot forge callbacks
- `PicklePersistence` ensures callback data survives gateway restarts (buttons remain functional)

**Stale button handling:** Register a handler for `InvalidCallbackData` exceptions — reply to the user: "This button has expired. Please wait for a new approval request."

**Approval message format:**

```
🔒 Permission Request

Action: ha_call_service(light.turn_on, light.bedroom)

[✓ Allow]  [✗ Deny]
```

The `Action` line shows the full engine signature, matching the format used in `permissions.yaml` rules. This lets the user see exactly what pattern is being matched.

**After approval:**

```
✅ Approved

Action: ha_call_service(light.turn_on, light.bedroom)

Approved by @username at 23:41
```

**After denial:**

```
❌ Denied

Action: ha_call_service(light.turn_on, light.bedroom)

Denied by @username at 23:42
```

**After timeout (15 min):**

```
⏰ Expired

Action: ha_call_service(light.turn_on, light.bedroom)

No response within 15 minutes — auto-denied.
```

**Timeout mechanism:**

Each pending approval registers an `asyncio.Task` with a 15-minute sleep. Both the timeout handler and the callback handler use the same atomic `resolve_request()` method to prevent race conditions:

```python
async def resolve_request(self, request_id: str, resolution: str, resolved_by: str) -> bool:
    """Atomically resolve a pending request. Returns False if already resolved."""
    async with self._resolve_lock:  # asyncio.Lock
        if request_id not in self._pending:
            return False  # Already resolved by the other path
        pending = self._pending.pop(request_id)
        if pending.timeout_task:
            pending.timeout_task.cancel()
        # ... execute action (if approved), update audit log, edit Telegram message
        return True
```

Both timeout and callback call `resolve_request()` — only the first one succeeds. The loser gets `False` and does nothing.

**Allowed users:** `config.messenger.telegram.allowed_users` is **required** — the gateway refuses to start without it. Only these Telegram user IDs can tap approval buttons. Callbacks from other users are silently ignored. This prevents unauthorized approvals if the chat group is later expanded.

---

### 6. messenger/base.py — MessengerAdapter ABC

Defined in [0-spec.md](./0-spec.md#messenger-adapter-interface). The Telegram implementation is the only adapter in v1. The abstraction exists so v2+ can add Slack, Discord, etc. without touching the core gateway.

---

### 7. db.py — SQLite Storage

Handles audit logging and pending request persistence.

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    request_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args TEXT NOT NULL,          -- JSON
    signature TEXT NOT NULL,     -- e.g. "ha_call_service(light.turn_on, light.bedroom)"
    decision TEXT NOT NULL,      -- "allow", "deny", "ask"
    resolution TEXT,             -- "executed", "denied_by_user", "denied_by_policy", "timeout", NULL (pending)
    resolved_by TEXT,            -- Telegram user ID or "policy" or "timeout"
    resolved_at TEXT,            -- ISO 8601
    execution_result TEXT,       -- JSON (service response or error)
    agent_id TEXT DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS pending_requests (
    request_id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    args TEXT NOT NULL,          -- JSON
    signature TEXT NOT NULL,
    message_id TEXT,             -- Telegram message ID (for editing)
    chat_id INTEGER,             -- Telegram chat ID (for editing on restart)
    result TEXT,                 -- JSON: queued result if agent was offline when resolved
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at TEXT NOT NULL
);

CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_tool ON audit_log(tool_name);
CREATE INDEX idx_pending_expires ON pending_requests(expires_at);
```

**Library:** `aiosqlite` (async SQLite wrapper)

**Pending request lifecycle:**
1. `ask` decision → INSERT into `pending_requests`
2. User approves/denies OR timeout → DELETE from `pending_requests`, INSERT into `audit_log` with resolution
3. On startup, clean up any stale `pending_requests` (expired while gateway was down)

---

### 8. config.py — Configuration Loading

Loads YAML configuration with environment variable substitution.

**Responsibilities:**
- Load `config.yaml` and `permissions.yaml`
- Substitute `${ENV_VAR}` patterns with environment variable values
- Validate required fields (fail fast on missing config)
- Return typed dataclasses

**Env var substitution:**

```python
import re, os

def _replacer(match):
    var = match.group(1)
    val = os.environ.get(var)
    if val is None:
        raise ConfigError(f"Environment variable {var} is not set")
    return val

def substitute_env_vars(obj):
    """Recursively substitute ${VAR} in all string values within a nested structure."""
    if isinstance(obj, str):
        return re.sub(r'\$\{(\w+)\}', _replacer, obj)
    elif isinstance(obj, dict):
        return {k: substitute_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [substitute_env_vars(item) for item in obj]
    return obj
```

Type coercion (str → int for `port`, `chat_id`) happens during dataclass construction.

**Config dataclasses:**

```python
@dataclass
class TLSConfig:
    cert: str
    key: str

@dataclass
class GatewayConfig:
    host: str
    port: int
    tls: TLSConfig | None = None  # None only with --insecure flag

@dataclass
class AgentConfig:
    token: str  # bearer token

@dataclass
class TelegramConfig:
    token: str
    chat_id: int                   # integer — Telegram chat IDs (negative for groups)
    allowed_users: list[int]       # required — Telegram user IDs who can approve

@dataclass
class MessengerConfig:
    type: str  # "telegram"
    telegram: TelegramConfig | None = None

@dataclass
class HomeAssistantConfig:
    url: str
    token: str

@dataclass
class StorageConfig:
    type: str  # "sqlite"
    path: str

@dataclass
class RateLimitConfig:
    max_pending_approvals: int = 10
    max_requests_per_minute: int = 60

@dataclass
class Config:
    gateway: GatewayConfig
    agent: AgentConfig
    messenger: MessengerConfig
    services: dict[str, HomeAssistantConfig]  # v1: only "homeassistant"
    storage: StorageConfig
    approval_timeout: int = 900  # 15 minutes in seconds
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
```

---

### 9. models.py — Shared Data Models

Dataclasses used across components.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import asyncio
import time

class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"

@dataclass
class ToolRequest:
    """Incoming tool request from an agent."""
    id: str
    tool_name: str
    args: dict[str, Any]
    signature: str = ""  # Filled by engine

@dataclass
class ToolResult:
    """Result of an executed tool request."""
    request_id: str
    status: str  # "executed" or "denied"
    data: dict[str, Any] | None = None

@dataclass
class PendingApproval:
    """A tool request awaiting human approval."""
    request: ToolRequest
    future: asyncio.Future          # Resolved when human approves/denies/timeout
    message_id: str | None = None   # Telegram message ID (for editing)
    timeout_task: asyncio.Task | None = None  # 15-min timeout task (cancelable)
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0           # Set by server based on config.approval_timeout

@dataclass
class AuditEntry:
    """A record of a tool request and its outcome."""
    request_id: str
    timestamp: float = field(default_factory=time.time)
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    signature: str = ""
    decision: str = ""
    resolution: str | None = None       # "executed", "denied_by_user", "denied_by_policy", "timeout"
    resolved_by: str | None = None      # Telegram user ID, "policy", or "timeout"
    resolved_at: float | None = None
    execution_result: dict[str, Any] | None = None
    agent_id: str = "default"           # Reserved for v2 multi-agent
```

---

### 10. client.py — Agent SDK

Thin WebSocket client that agents use to talk to the gateway.

```python
class AgentGateClient:
    """Client SDK for agents connecting to an agent-gate gateway."""

    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self._ws = None
        self._request_counter = 0
        self._pending: dict[str, asyncio.Future] = {}

    async def connect(self):
        """Connect to the gateway and authenticate."""
        self._ws = await websockets.connect(self.url)
        await self._authenticate()
        self._reader_task = asyncio.create_task(self._read_loop())

    async def tool_request(self, tool: str, **args) -> dict:
        """Send a tool request and wait for the result.

        Blocks until the gateway responds (may take up to approval_timeout
        if human approval is required).

        Raises:
            AgentGateDenied: If denied by policy or by user.
            AgentGateTimeout: If approval timed out.
            AgentGateError: For other errors.
        """
        request_id = self._next_id()
        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        await self._ws.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "tool_request",
            "params": {"tool": tool, "args": args},
            "id": request_id,
        }))

        return await future

    async def close(self):
        """Disconnect from the gateway."""
        ...

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()
```

**Error classes:**

```python
class AgentGateError(Exception):
    """Base error for agent-gate SDK."""
    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(message)

class AgentGateDenied(AgentGateError):
    """Tool request was denied (by policy or by user)."""

class AgentGateTimeout(AgentGateError):
    """Approval request timed out."""
```

---

### 11. __main__.py — CLI Entrypoint

Orchestrates the startup of all async components on a single event loop.

**IMPORTANT:** PTB v21's `Application.run_polling()` calls `asyncio.run()` internally and cannot coexist with other async code. We use manual lifecycle methods instead.

```python
"""python -m agent_gate"""
import asyncio
import logging
import signal
import ssl
from agent_gate.config import load_config, load_permissions
from agent_gate.db import Database
from agent_gate.engine import PermissionEngine
from agent_gate.executor import Executor
from agent_gate.messenger.telegram import TelegramAdapter
from agent_gate.services.homeassistant import HomeAssistantService

logger = logging.getLogger("agent_gate")

async def run(config, permissions):
    stop_event = asyncio.Event()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    # 1. Initialize database
    db = Database(config.storage.path)
    await db.initialize()
    await db.cleanup_stale_requests()

    # 2. Initialize services + health checks
    ha = HomeAssistantService(config.services["homeassistant"])
    if not await ha.health_check():
        logger.warning("Home Assistant unreachable — continuing anyway")
    executor = Executor({"ha": ha})

    # 3. Initialize permission engine
    engine = PermissionEngine(permissions)

    # 4. Initialize Telegram (manual lifecycle — NOT run_polling)
    telegram = TelegramAdapter(config.messenger.telegram)
    ptb_app = telegram.application

    async with ptb_app:
        await ptb_app.start()
        await ptb_app.updater.start_polling()

        # 5. Start WebSocket server
        ssl_ctx = None
        if config.gateway.tls:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(config.gateway.tls.cert, config.gateway.tls.key)

        import websockets
        async with websockets.serve(
            lambda ws: handle_connection(ws, config, engine, executor, telegram, db),
            config.gateway.host,
            config.gateway.port,
            ssl=ssl_ctx,
        ):
            proto = "wss" if ssl_ctx else "ws"
            logger.info(f"agent-gate ready on {proto}://{config.gateway.host}:{config.gateway.port}")
            await stop_event.wait()

        # Shutdown
        logger.info("Shutting down...")
        await ptb_app.updater.stop()
        await ptb_app.stop()
    await ha.close()
    await db.close()

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = load_config()
    permissions = load_permissions()
    asyncio.run(run(config, permissions))

if __name__ == "__main__":
    main()
```

---

## Request Lifecycle

Complete flow for an `ask` decision:

```
1.  Agent → WS → server.py: tool_request("ha_call_service", {...})
2.  server.py → engine.py: evaluate("ha_call_service", {...})
3.  engine.py: build signature "ha_call_service(light.turn_on, light.bedroom)"
4.  engine.py: match against rules → Decision.ASK
5.  server.py: create PendingApproval, store in memory + SQLite
6.  server.py → messenger/telegram.py: send_approval(request, choices)
7.  telegram.py → Telegram API: sendMessage with inline keyboard
8.  telegram.py → server.py: returns message_id
9.  server.py: starts 15-min timeout task
10. (time passes — agent's WebSocket request is still open, waiting)
11. User taps [✓ Allow] in Telegram
12. Telegram API → telegram.py: callback query
13. telegram.py: validates user is in allowed_users (required)
14. telegram.py → server.py: on_approval_callback(ApprovalResult)
15. server.py: cancel timeout task
16. server.py → executor.py: execute("ha_call_service", {...})
17. executor.py → services/homeassistant.py: POST /api/services/light/turn_on
18. homeassistant.py → executor.py: returns HA response
19. server.py → db.py: INSERT audit_log entry
20. server.py: DELETE from pending_requests
21. server.py → messenger/telegram.py: update_approval(message_id, "approved", ...)
22. telegram.py → Telegram API: editMessageText
23. server.py → WS → Agent: JSON-RPC result {"status": "executed", "data": {...}}
```

## Startup Sequence

```
1.  Load config.yaml + permissions.yaml (fail fast on missing/invalid)
2.  Validate config (required fields, allowed_users non-empty, TLS certs exist)
3.  Initialize SQLite database (create tables if not exist, set file perms 0600)
4.  Clean up stale pending_requests:
    a. Edit orphaned Telegram messages: "⚠️ Gateway restarted — please re-request"
    b. Move expired pending_requests to audit_log with resolution "gateway_restart"
5.  Initialize service handlers (HomeAssistantService)
6.  Run health checks (non-blocking, log warnings only):
    - HA: GET /api/ with 5s timeout
    - Telegram: bot.get_me()
7.  Initialize Telegram adapter with PicklePersistence
8.  Start PTB (app.start() + updater.start_polling()) — manual lifecycle
9.  Start WebSocket server (with TLS if configured)
10. Log: "agent-gate ready on wss://0.0.0.0:8443"
```

## Shutdown Sequence

```
1.  Receive SIGTERM/SIGINT
2.  Stop accepting new WebSocket connections
3.  Resolve all pending approvals as "gateway_shutdown" (deny)
4.  Edit pending Telegram messages: "⚠️ Gateway shutting down"
5.  Close WebSocket connections
6.  Stop Telegram polling
7.  Close aiohttp sessions (HA client)
8.  Close SQLite connection
9.  Exit
```

## Concurrency Model

The gateway runs on a single asyncio event loop (`asyncio.run()` in `__main__.py`):

- **WebSocket server:** `websockets.serve()` — handles one agent connection
- **Telegram polling:** PTB's manual lifecycle (`app.start()` + `updater.start_polling()`) — shares the same event loop (NOT `run_polling()` which would create its own)
- **Timeout tasks:** `asyncio.create_task()` per pending approval, with `asyncio.Lock` for race-safe resolution
- **Service calls:** `aiohttp.ClientSession` — non-blocking HTTP to HA
- **Rate limiter:** In-memory token bucket checked before dispatching each request

No threads or multiprocessing needed in v1. Everything is async/await.

## Agent Disconnect Recovery

If the agent disconnects while approvals are pending (Wi-Fi drop, Pi reboot, crash):

1. **Pending approvals continue** — the user can still tap buttons in Telegram
2. **If approved while disconnected:** gateway executes the action, stores the result in SQLite (`pending_requests.result` column), edits Telegram message: "Executed (agent offline — result queued)"
3. **On reconnection:** after auth, the agent can call `get_pending_results` to retrieve any queued results:

```jsonc
// Agent → Gateway: check for queued results after reconnect
{"jsonrpc": "2.0", "method": "get_pending_results", "id": "reconn-1"}

// Gateway → Agent: queued results (if any)
{
  "jsonrpc": "2.0",
  "result": {
    "queued": [
      {"request_id": "req-042", "status": "executed", "data": {...}},
      {"request_id": "req-043", "status": "denied", "data": null}
    ]
  },
  "id": "reconn-1"
}
```

4. **Timeout still applies** — if the 15-minute window expires before the user responds, the request is denied as usual (agent offline or not)

## Rate Limiting

Basic in-memory rate limiting to protect against a compromised Pi:

| Limit | Default | Error Code |
|---|---|---|
| Max pending approvals | 10 | -32006 "Too many pending approvals" |
| Max requests per minute (auto-allowed) | 60 | -32006 "Rate limit exceeded" |
| Max connection attempts per minute | 5 | Connection refused |

Implemented as a simple token bucket (per-agent in v2, global in v1). Telegram message sending is additionally rate-limited to stay under the Bot API's ~30 msg/sec limit.

## Security Boundaries

```
┌─────────────────────────────────────┐
│         TRUST BOUNDARY              │
│                                     │
│  Untrusted:                         │
│  - Agent device (Pi)                │
│  - Agent's tool_request content     │
│                                     │
├─────────────────────────────────────┤
│                                     │
│  Trusted:                           │
│  - Gateway process                  │
│  - config.yaml / permissions.yaml   │
│  - Service credentials (HA token)   │
│  - Guardian bot token               │
│  - SQLite database                  │
│  - TLS certificates                 │
│                                     │
└─────────────────────────────────────┘
```

**What the Pi can do (even if fully compromised):**
- Send arbitrary tool requests (but policies still apply)
- Flood the gateway with requests (mitigated by rate limiting: max 10 pending, max 60/min)
- Disconnect/reconnect
- Send requests with crafted arguments (mitigated by input validation)

**What the Pi CANNOT do:**
- Access service credentials (HA token, Guardian bot token)
- Forge Telegram approval callbacks (different bot token, PTB validates with `arbitrary_callback_data`)
- Bypass the permission engine
- Execute actions directly on HA (no token)
- Sniff the bearer token (WSS/TLS required)

## pyproject.toml

```toml
[project]
name = "agent-gate"
version = "0.1.0"
description = "An execution gateway for AI agents on untrusted devices"
requires-python = ">=3.12"
license = "MIT"
dependencies = [
    "websockets>=14.0,<17.0",
    "python-telegram-bot[callback-data]>=21.0,<22.0",
    "aiosqlite>=0.20.0,<1.0",
    "aiohttp>=3.10.0,<4.0",
    "pyyaml>=6.0,<7.0",
]

[project.scripts]
agent-gate = "agent_gate.__main__:main"

[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.setuptools.packages.find]
where = ["src"]
```

Note: `python-telegram-bot[callback-data]` installs the `cachetools` extra needed for `arbitrary_callback_data=True`.

## Docker

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
COPY config.example.yaml permissions.example.yaml ./
VOLUME ["/app/data"]
EXPOSE 8443
CMD ["agent-gate"]
```

```yaml
# docker-compose.yml
services:
  agent-gate:
    build: .
    ports:
      - "8443:8443"
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./permissions.yaml:/app/permissions.yaml:ro
      - ./data:/app/data                   # SQLite + PicklePersistence
      - ./certs:/app/certs:ro              # TLS certificates
    env_file: .env
    restart: unless-stopped
```
