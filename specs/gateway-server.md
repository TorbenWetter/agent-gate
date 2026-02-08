# Feature: Gateway Server

> WebSocket server, Telegram Guardian bot, Home Assistant REST client, and CLI orchestration — the networking layer that connects agents to services through human-approved policies.

## Overview

Spec 2 builds the networking layer on top of Spec 1's pure-logic core. It implements:

- A **WebSocket server** (JSON-RPC 2.0) that authenticates agents, dispatches tool requests through the permission engine, and holds long-lived connections for pending human approvals.
- A **Telegram Guardian bot** that sends inline-keyboard approval messages, handles callbacks, manages timeouts, and edits messages to reflect decisions.
- A **Home Assistant REST client** that maps tool names to HA API calls and returns results.
- A **CLI entrypoint** that orchestrates startup, shutdown, and signal handling with PTB's manual lifecycle pattern.

All modules depend on Spec 1 (models, config, engine, db, executor, services/base) which is fully implemented and tested.

## Requirements

### Functional Requirements

- [ ] FR1: WebSocket server accepts agent connections on configured host:port with TLS (WSS) by default
  - **Acceptance Criteria:**
  - [ ] FR1-AC1: Server listens on `config.gateway.host:config.gateway.port` using WSS when TLS cert/key are configured
  - [ ] FR1-AC2: Server accepts plaintext WS only when `--insecure` flag is passed
  - [ ] FR1-AC3: Server rejects a second concurrent agent connection (v1: single agent)

- [ ] FR2: Agent authentication via JSON-RPC `auth` method with 10-second deadline
  - **Acceptance Criteria:**
  - [ ] FR2-AC1: First message must be `{"method": "auth", "params": {"token": "..."}}` — validated against `config.agent.token`
  - [ ] FR2-AC2: Auth must complete within 10 seconds of connection or server closes with -32005 error
  - [ ] FR2-AC3: Any non-auth message before authentication returns -32005 error and closes connection
  - [ ] FR2-AC4: Successful auth responds with `{"result": {"status": "authenticated"}}`

- [ ] FR3: Tool request processing via JSON-RPC `tool_request` method
  - **Acceptance Criteria:**
  - [ ] FR3-AC1: Parses `{"method": "tool_request", "params": {"tool": "...", "args": {...}}}` into a ToolRequest
  - [ ] FR3-AC2: Evaluates request through PermissionEngine; `allow` executes immediately, `deny` returns -32003 error
  - [ ] FR3-AC3: `ask` decision triggers Telegram approval flow; WebSocket response is deferred until human decision or timeout
  - [ ] FR3-AC4: Multiple concurrent tool_requests are supported (each gets its own asyncio.Task)
  - [ ] FR3-AC5: Malformed JSON returns -32700; missing required fields return -32600

- [ ] FR4: Rate limiting with two independent dimensions
  - **Acceptance Criteria:**
  - [ ] FR4-AC1: All incoming requests (before engine evaluation) are limited to `config.rate_limit.max_requests_per_minute` (default 60) using a sliding window
  - [ ] FR4-AC2: Pending approvals (after engine returns `ask`) are limited to `config.rate_limit.max_pending_approvals` (default 10)
  - [ ] FR4-AC3: Exceeding either limit returns -32006 error with descriptive message

- [ ] FR5: Telegram Guardian bot sends approval messages with inline keyboard
  - **Acceptance Criteria:**
  - [ ] FR5-AC1: Sends formatted approval message with tool signature and `[Allow]` / `[Deny]` inline buttons
  - [ ] FR5-AC2: Only callbacks from user IDs in `config.messenger.telegram.allowed_users` are processed; others silently ignored
  - [ ] FR5-AC3: Uses `arbitrary_callback_data=True` with `PicklePersistence` for callback data survival across restarts
  - [ ] FR5-AC4: Handles `InvalidCallbackData` from stale buttons gracefully (answer callback with "expired" message)
  - [ ] FR5-AC5: Edits approval message after decision: "Approved by @user at HH:MM" or "Denied by @user at HH:MM"

- [ ] FR6: Approval timeout and message editing
  - **Acceptance Criteria:**
  - [ ] FR6-AC1: Each pending approval starts an `asyncio.Task` that fires after `config.approval_timeout` seconds
  - [ ] FR6-AC2: Timeout resolves the request as denied with -32002 error
  - [ ] FR6-AC3: Best-effort edit of Telegram message to show "Expired" on timeout (log warning on failure, never block)
  - [ ] FR6-AC4: `asyncio.Lock` ensures race-safe resolution between callback and timeout (first one wins)

- [ ] FR7: Home Assistant REST client implements ServiceHandler
  - **Acceptance Criteria:**
  - [ ] FR7-AC1: Maps `ha_get_state` → `GET /api/states/{entity_id}`, `ha_get_states` → `GET /api/states`, `ha_call_service` → `POST /api/services/{domain}/{service}`, `ha_fire_event` → `POST /api/events/{event_type}`
  - [ ] FR7-AC2: Sends HA long-lived access token as `Authorization: Bearer` header
  - [ ] FR7-AC3: Health check is `GET /api/` with 5-second timeout; returns True/False without raising
  - [ ] FR7-AC4: Fails immediately on HA errors (no retry): HTTP 401 → -32004, HTTP 404 → -32004, connection refused → -32004
  - [ ] FR7-AC5: Manages `aiohttp.ClientSession` lifecycle (create on init, close on shutdown)

- [ ] FR8: Pending approval persistence across gateway restarts
  - **Acceptance Criteria:**
  - [ ] FR8-AC1: Pending approvals are stored in SQLite `pending_requests` table (already created by Spec 1 db.py)
  - [ ] FR8-AC2: On startup, non-expired pending requests are loaded from SQLite and approval timeout tasks are recreated
  - [ ] FR8-AC3: When agent disconnects and guardian approves/denies, the decision + execution result are stored in `pending_requests.result`
  - [ ] FR8-AC4: Agent can retrieve stored results via `get_pending_results` method after reconnection

- [ ] FR9: Messenger adapter abstraction
  - **Acceptance Criteria:**
  - [ ] FR9-AC1: `MessengerAdapter` ABC defines `send_approval()`, `update_approval()`, `on_approval_callback()`, `start()`, `stop()`
  - [ ] FR9-AC2: `TelegramAdapter` implements `MessengerAdapter` using PTB v21
  - [ ] FR9-AC3: `ApprovalRequest`, `ApprovalChoice`, `ApprovalResult` dataclasses defined in messenger/base.py

- [ ] FR10: CLI entrypoint with orchestrated startup and graceful shutdown
  - **Acceptance Criteria:**
  - [ ] FR10-AC1: Supports `--insecure`, `--config PATH` (default: config.yaml), `--permissions PATH` (default: permissions.yaml) flags
  - [ ] FR10-AC2: Startup sequence: config → db → services → health checks → PTB start → WS serve → log "ready"
  - [ ] FR10-AC3: Signal handling (SIGTERM, SIGINT) triggers graceful shutdown
  - [ ] FR10-AC4: Shutdown sequence: resolve pending as "gateway_shutdown", edit Telegram messages, stop WS → stop PTB → close HA → close DB
  - [ ] FR10-AC5: PTB uses manual lifecycle (`async with app: ... start() ... start_polling()`) — NOT `run_polling()`

- [ ] FR11: Audit logging for all request lifecycle events
  - **Acceptance Criteria:**
  - [ ] FR11-AC1: Every tool request is logged with decision, regardless of outcome (allow, deny, ask)
  - [ ] FR11-AC2: When an `ask` request is resolved (approved, denied, timed out, gateway shutdown), the audit entry is updated with resolution, resolved_by, resolved_at, and execution_result

### Non-Functional Requirements

- [ ] NFR1: Security — WSS (TLS) required by default; plaintext only with explicit `--insecure` flag
  - **Acceptance Criteria:**
  - [ ] NFR1-AC1: Server refuses to start without TLS config unless `--insecure` is passed
  - [ ] NFR1-AC2: Agent token never logged or included in error messages

- [ ] NFR2: Concurrency — Single asyncio event loop shared between WS server, PTB, and aiohttp
  - **Acceptance Criteria:**
  - [ ] NFR2-AC1: No threads or multiprocessing; everything runs in one event loop
  - [ ] NFR2-AC2: Long-running operations (HA calls, Telegram API) are non-blocking (async/await)

- [ ] NFR3: Resilience — Gateway continues operating when HA is temporarily unreachable
  - **Acceptance Criteria:**
  - [ ] NFR3-AC1: Failed HA health check on startup logs a warning but does not prevent startup
  - [ ] NFR3-AC2: HA call failures return errors to the agent but do not crash the gateway

- [ ] NFR4: Observability — Structured logging at appropriate levels
  - **Acceptance Criteria:**
  - [ ] NFR4-AC1: INFO: startup/shutdown, agent connect/disconnect, approval decisions
  - [ ] NFR4-AC2: WARNING: HA unreachable, Telegram edit failures, stale callback data
  - [ ] NFR4-AC3: ERROR: unexpected exceptions, auth failures

- [ ] NFR5: Testability — All components testable with mocked external services
  - **Acceptance Criteria:**
  - [ ] NFR5-AC1: WebSocket server testable with real connections (websockets client) and mocked dependencies
  - [ ] NFR5-AC2: Telegram adapter testable with mocked PTB Bot (no real Telegram API calls)
  - [ ] NFR5-AC3: HA client testable with mocked aiohttp responses

## Technical Design

### Affected Components

- `src/agent_gate/server.py` — **NEW** — WebSocket server, JSON-RPC parsing, auth flow, pending request management, rate limiting
- `src/agent_gate/messenger/base.py` — **NEW** — MessengerAdapter ABC, ApprovalRequest/Choice/Result dataclasses
- `src/agent_gate/messenger/telegram.py` — **NEW** — TelegramAdapter implementing MessengerAdapter with PTB v21
- `src/agent_gate/services/homeassistant.py` — **NEW** — HomeAssistantService implementing ServiceHandler
- `src/agent_gate/__main__.py` — **NEW** — CLI entrypoint, argparse, orchestration, signal handling, shutdown

### Data Model

**New dataclasses in messenger/base.py:**

```python
@dataclass
class ApprovalRequest:
    request_id: str
    tool_name: str
    args: dict
    signature: str  # human-readable tool signature

@dataclass
class ApprovalChoice:
    label: str      # "Allow", "Deny"
    action: str     # "allow", "deny"

@dataclass
class ApprovalResult:
    request_id: str
    action: str     # "allow", "deny"
    user_id: str    # Telegram user ID as string
    timestamp: float
```

**Existing models used (from Spec 1):**

- `Decision` — permission engine output
- `ToolRequest` — parsed from JSON-RPC params
- `ToolResult` — returned to agent
- `PendingApproval` — in-memory tracking (future, timeout_task, message_id)
- `AuditEntry` — logged to SQLite

### WebSocket Protocol (JSON-RPC 2.0)

**Error codes:**

| Code   | Meaning                     |
|--------|-----------------------------|
| -32700 | Parse error (malformed JSON) |
| -32600 | Invalid request (missing fields, validation) |
| -32601 | Method not found            |
| -32001 | Approval denied by user     |
| -32002 | Approval timed out          |
| -32003 | Policy denied (no human)    |
| -32004 | Action execution failed     |
| -32005 | Not authenticated           |
| -32006 | Rate limit exceeded         |

**Methods:**

| Method               | Direction        | Purpose |
|----------------------|------------------|---------|
| `auth`               | Agent → Gateway  | Authenticate with bearer token |
| `tool_request`       | Agent → Gateway  | Request tool execution |
| `get_pending_results`| Agent → Gateway  | Retrieve stored results after reconnect |

### Dependencies (Spec 1 → Spec 2)

| Spec 1 Module    | Used By (Spec 2)         | Purpose |
|------------------|--------------------------|---------|
| `config.py`      | `__main__.py`            | Load config + permissions |
| `engine.py`      | `server.py`              | Evaluate tool requests |
| `db.py`          | `server.py`, `__main__.py` | Audit log, pending requests |
| `executor.py`    | `server.py`              | Execute allowed tools |
| `models.py`      | All Spec 2 modules       | Shared data types |
| `services/base.py` | `homeassistant.py`     | ServiceHandler ABC |

### Rate Limiter Design

Two independent checks:

1. **Request rate** (before engine): Sliding window counter. Each incoming `tool_request` increments the counter. If > `max_requests_per_minute`, return -32006 immediately.

2. **Pending count** (after engine returns `ask`): Count of currently pending approvals. If >= `max_pending_approvals`, return -32006 immediately.

### Telegram Message Lifecycle

```
Agent sends tool_request
  → Engine returns ASK
    → Server creates PendingApproval (in-memory + SQLite)
    → Server calls messenger.send_approval()
      → Telegram sends message with [Allow] [Deny] buttons
      → Returns message_id
    → Server starts timeout task
    → [User taps Allow]
      → PTB fires callback_query handler
      → Adapter calls registered callback with ApprovalResult
      → Server resolves: cancel timeout, execute tool, update audit, edit message
      → Server sends JSON-RPC result to agent (or stores in SQLite if disconnected)
```

### Pickle Persistence Path

Derived from `config.storage.path` — same directory as the SQLite file:

```python
storage_dir = Path(config.storage.path).parent
pickle_path = storage_dir / "callback_data.pickle"
```

## Implementation Plan

### Task Decomposition

**T1: Messenger adapter base (messenger/base.py)**
- Depends on: nothing
- Files owned: `src/agent_gate/messenger/base.py`
- Acceptance criteria: FR9-AC1, FR9-AC3

**T2: Home Assistant client (services/homeassistant.py)**
- Depends on: nothing (only services/base.py from Spec 1)
- Files owned: `src/agent_gate/services/homeassistant.py`
- Acceptance criteria: FR7-AC1, FR7-AC2, FR7-AC3, FR7-AC4, FR7-AC5

**T3: Telegram adapter (messenger/telegram.py)**
- Depends on: T1 (messenger/base.py)
- Files owned: `src/agent_gate/messenger/telegram.py`
- Acceptance criteria: FR5-AC1, FR5-AC2, FR5-AC3, FR5-AC4, FR5-AC5, FR6-AC1, FR6-AC2, FR6-AC3, FR6-AC4

**T4: WebSocket server (server.py)**
- Depends on: T1 (for messenger interaction types)
- Files owned: `src/agent_gate/server.py`
- Acceptance criteria: FR1-AC1, FR1-AC2, FR1-AC3, FR2-AC1, FR2-AC2, FR2-AC3, FR2-AC4, FR3-AC1, FR3-AC2, FR3-AC3, FR3-AC4, FR3-AC5, FR4-AC1, FR4-AC2, FR4-AC3, FR8-AC1, FR8-AC2, FR8-AC3, FR8-AC4, FR11-AC1, FR11-AC2

**T5: CLI entrypoint (__main__.py)**
- Depends on: T2, T3, T4 (all components must exist)
- Files owned: `src/agent_gate/__main__.py`
- Acceptance criteria: FR10-AC1, FR10-AC2, FR10-AC3, FR10-AC4, FR10-AC5, NFR1-AC1, NFR1-AC2, NFR3-AC1

### Dependency Graph

```
T1 (messenger/base.py)  ──→  T3 (telegram.py)  ──→  T5 (__main__.py)
                              T4 (server.py)     ──→  T5
T2 (homeassistant.py)   ──────────────────────────→  T5
```

T1 and T2 are independent (parallel). T3 and T4 depend on T1. T5 depends on T2, T3, T4.

## Test Plan

### Unit Tests

**test_messenger_base.py (T1):**
- [ ] ApprovalRequest, ApprovalChoice, ApprovalResult dataclass construction
- [ ] MessengerAdapter is abstract and cannot be instantiated

**test_homeassistant.py (T2):**
- [ ] ha_get_state maps to GET /api/states/{entity_id}
- [ ] ha_get_states maps to GET /api/states
- [ ] ha_call_service maps to POST /api/services/{domain}/{service} with correct body
- [ ] ha_fire_event maps to POST /api/events/{event_type} with correct body
- [ ] Authorization header includes Bearer token
- [ ] Health check returns True on 200, False on connection error
- [ ] HTTP 401 raises appropriate error
- [ ] HTTP 404 raises appropriate error
- [ ] Connection refused raises appropriate error
- [ ] Session cleanup on close()

**test_telegram.py (T3):**
- [ ] send_approval formats message correctly with signature and buttons
- [ ] Callback from allowed user triggers registered callback
- [ ] Callback from non-allowed user is silently ignored
- [ ] update_approval edits message with correct text
- [ ] Timeout task fires after configured delay
- [ ] asyncio.Lock prevents double resolution (callback vs timeout race)
- [ ] InvalidCallbackData handled gracefully
- [ ] PicklePersistence path derived from storage directory

**test_server.py (T4) — Mock WebSocket layer:**
- [ ] Auth with correct token succeeds
- [ ] Auth with wrong token returns -32005 and closes
- [ ] Auth timeout (>10s) returns -32005 and closes
- [ ] Non-auth message before auth returns -32005 and closes
- [ ] tool_request with allow decision returns executed result
- [ ] tool_request with deny decision returns -32003 error
- [ ] tool_request with ask decision defers response
- [ ] Malformed JSON returns -32700
- [ ] Missing method returns -32601
- [ ] Missing params fields returns -32600
- [ ] Rate limit exceeded returns -32006
- [ ] Pending approval limit exceeded returns -32006
- [ ] get_pending_results returns stored results
- [ ] Second concurrent connection is rejected

**test_server.py — Real WebSocket integration tests:**
- [ ] Full auth handshake over real WebSocket connection
- [ ] tool_request → allow → result over real WebSocket
- [ ] tool_request → deny → error over real WebSocket
- [ ] Malformed JSON over real WebSocket returns -32700

**test_main.py (T5):**
- [ ] argparse: --insecure, --config, --permissions flags parsed correctly
- [ ] argparse: defaults are config.yaml and permissions.yaml
- [ ] Startup sequence calls components in correct order
- [ ] Missing TLS config without --insecure prevents startup
- [ ] Failed HA health check logs warning but doesn't stop startup
- [ ] Signal handler sets stop event

### Manual Testing

- [ ] Connect with a real WebSocket client and complete auth + tool request
- [ ] Trigger Telegram approval and verify message editing
- [ ] Verify timeout behavior with a real pending approval
- [ ] Test gateway restart: pending request persists, agent retrieves result

## Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Agent sends auth with wrong token | -32005 error, connection closed |
| Agent never sends auth | 10-second timeout, -32005 error, connection closed |
| Agent sends tool_request before auth | -32005 error, connection closed |
| Agent disconnects mid-approval | Approval continues; result stored in SQLite for retrieval |
| Guardian approves after agent disconnected | Execute action, store result; agent retrieves via get_pending_results |
| Two approvals race (callback + timeout) | asyncio.Lock ensures first wins; second is no-op |
| Telegram edit fails (message too old) | Log warning, continue; never block resolution |
| HA returns 401 (expired token) | -32004 error returned to agent |
| HA unreachable on startup | Log warning, continue startup; calls will fail when attempted |
| HA unreachable during tool execution | -32004 error returned to agent immediately (no retry) |
| Agent floods with requests | 60/min limit → -32006; 10 pending limit → -32006 |
| Gateway receives SIGTERM during approval | Resolve all pending as "gateway_shutdown", edit messages, shut down |
| Stale Telegram callback button (from before restart) | InvalidCallbackData handled; answer callback with "expired" text |
| Malformed JSON-RPC (missing jsonrpc field, wrong version) | -32600 error |
| Agent sends unknown method | -32601 error |
| Concurrent agent connection attempt | Reject with WebSocket close code |

## Open Questions

_None — all questions resolved during discovery._

## Decision Log

| Decision | Rationale | Date |
|----------|-----------|------|
| Error + close on auth failure | Simpler state machine; forces clean reconnect | 2026-02-08 |
| Best-effort Telegram edits on timeout | Keeps chat tidy when it works; fire-and-forget avoids blocking resolution | 2026-02-08 |
| Pending approvals survive restart (SQLite) | Matches existing DB schema; agent can reconnect and retrieve results | 2026-02-08 |
| No HA retry (fail immediately) | Simpler, no hidden latency; agent/SDK can implement retry logic | 2026-02-08 |
| Both rate limits independent | 60/min prevents flooding even with all-allow; 10 pending limits Telegram spam | 2026-02-08 |
| Store offline approval results in SQLite | Agent retrieves via get_pending_results; matches pending_requests.result column | 2026-02-08 |
| Pickle path derived from storage path | One volume mount covers both SQLite and pickle; no extra config field | 2026-02-08 |
| CLI: --insecure + --config + --permissions | Flexible deployment; reasonable defaults (config.yaml, permissions.yaml) | 2026-02-08 |
| Concurrent tool_request processing | Each request gets own asyncio.Task; matches max_pending_approvals design | 2026-02-08 |
| Telegram: unit test adapter + mock Bot | Full PTB mock coverage is brittle; test adapter logic, defer integration to Spec 3 | 2026-02-08 |
| WS tests: both mock and real connections | Unit tests for logic speed, integration tests for protocol correctness | 2026-02-08 |
