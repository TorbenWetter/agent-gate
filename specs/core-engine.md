# Feature: Core Engine

> Pure-logic foundation for agent-gate: models, config loading, permission engine, SQLite storage, and action dispatch — all independently testable with no network dependencies.

## Overview

The core engine is the first implementation phase of agent-gate. It provides the domain models, configuration loading, permission evaluation, persistent storage, and action routing that all networking layers (WebSocket server, Telegram bot, HA client) depend on. Every module in this phase is pure logic or async-local I/O (SQLite), with no network calls.

## Requirements

### Functional Requirements

- [ ] FR1: Define shared data models (`Decision`, `ToolRequest`, `ToolResult`, `PendingApproval`, `AuditEntry`) as Python dataclasses/enums
- [ ] FR2: Load `config.yaml` with recursive `${ENV_VAR}` substitution, validate required fields, return typed `Config` dataclass
- [ ] FR3: Load `permissions.yaml` into typed `PermissionRule` and `PermissionDefaults` dataclasses via `config.py`
- [ ] FR4: Build deterministic tool call signatures per tool type using explicit builders (`ha_call_service`, `ha_get_state`, `ha_get_states`, `ha_fire_event`) with sorted-key fallback for unknown tools
- [ ] FR5: Validate arguments — reject values containing `* ? [ ] ( ) , \x00-\x1f`; validate HA identifiers against `^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)?$`
- [ ] FR6: Evaluate permissions with strict precedence: deny rules → allow rules → ask rules → defaults (first match) → fallback (ask)
- [ ] FR7: Create SQLite schema (`audit_log`, `pending_requests` tables + indexes) on initialization
- [ ] FR8: CRUD operations for `pending_requests` (insert, get, delete, cleanup stale)
- [ ] FR9: Insert and query `audit_log` entries with ISO 8601 timestamps
- [ ] FR10: Route tool requests to the correct service handler via `TOOL_SERVICE_MAP`, reject unknown tools
- [ ] FR11: Define `ServiceHandler` ABC with `execute()`, `health_check()`, and `close()` methods

### Non-Functional Requirements

- [ ] NFR1: All modules testable without network — no imports of `websockets`, `telegram`, `aiohttp` in core engine
- [ ] NFR2: Config loading fails fast on missing required fields or unset env vars
- [ ] NFR3: SQLite database file permissions set to 0600 on creation
- [ ] NFR4: Signature building is deterministic regardless of dict insertion order
- [ ] NFR5: Permission evaluation completes in O(n) where n = number of rules (no nested loops)

## Technical Design

### Affected Components

- `src/agent_gate/models.py` — All shared dataclasses and enums
- `src/agent_gate/config.py` — YAML loading, env var substitution, validation, permission parsing
- `src/agent_gate/engine.py` — Signature builders, input validation, permission evaluation
- `src/agent_gate/db.py` — SQLite schema, audit log, pending request CRUD
- `src/agent_gate/executor.py` — TOOL_SERVICE_MAP, Executor class, dispatch routing
- `src/agent_gate/services/base.py` — ServiceHandler ABC

### Module Details

#### models.py

```python
# Exact types from architecture doc:
class Decision(Enum): ALLOW, DENY, ASK
class ToolRequest: id, tool_name, args, signature
class ToolResult: request_id, status, data
class PendingApproval: request, future, message_id, timeout_task, created_at, expires_at
class AuditEntry: request_id, timestamp, tool_name, args, signature, decision, resolution, resolved_by, resolved_at, execution_result, agent_id
```

`PendingApproval.future` and `PendingApproval.timeout_task` reference `asyncio` types — these are runtime-only (not stored in DB). The DB stores the serializable subset.

#### config.py

**Config loading:**
1. `load_config(path: str = "config.yaml") -> Config` — load YAML, substitute env vars, construct typed dataclasses, validate
2. `load_permissions(path: str = "permissions.yaml") -> Permissions` — load YAML, substitute env vars, parse into `Permissions` dataclass

**Env var substitution:** Recursive `${VAR}` replacement in all string values. Missing env var → `ConfigError` (fail fast).

**Typed config dataclasses** (from architecture doc):
- `TLSConfig(cert, key)`
- `GatewayConfig(host, port, tls?)`
- `AgentConfig(token)`
- `TelegramConfig(token, chat_id: int, allowed_users: list[int])`
- `MessengerConfig(type, telegram?)`
- `HomeAssistantConfig(url, token)`
- `StorageConfig(type, path)`
- `RateLimitConfig(max_pending_approvals=10, max_requests_per_minute=60)`
- `Config(gateway, agent, messenger, services, storage, approval_timeout=900, rate_limit)`

**Permission dataclasses:**
- `PermissionRule(pattern: str, action: str, description: str = "")`
- `Permissions(defaults: list[PermissionRule], rules: list[PermissionRule])`

**Validation rules:**
- `gateway.host` and `gateway.port` required
- `agent.token` required, non-empty
- `messenger.type` must be `"telegram"`
- `messenger.telegram.token`, `chat_id`, `allowed_users` required; `allowed_users` must be non-empty list of ints
- `services.homeassistant.url` and `token` required
- `storage.type` must be `"sqlite"`; `storage.path` required
- `approval_timeout` must be positive integer if provided
- Type coercion: `port` and `chat_id` are coerced from string to int (YAML may parse env-substituted values as strings)

#### engine.py

**Signature builders:**

| Tool | Signature format | Example |
|------|-----------------|---------|
| `ha_call_service` | `ha_call_service({domain}.{service}, {entity_id})` | `ha_call_service(light.turn_on, light.bedroom)` |
| `ha_get_state` | `ha_get_state({entity_id})` | `ha_get_state(sensor.living_room_temp)` |
| `ha_get_states` | `ha_get_states` (no parens) | `ha_get_states` |
| `ha_fire_event` | `ha_fire_event({event_type})` | `ha_fire_event(custom_event)` |
| Unknown | `tool_name({sorted_values})` | `custom_tool(arg1_val, arg2_val)` |

**Input validation (before signature building):**
- `FORBIDDEN_CHARS_RE = re.compile(r"[*?\[\](),\x00-\x1f]")`
- `HA_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)?$")`
- HA identifier check applies to: `entity_id`, `domain`, `service`, `event_type` fields when tool starts with `ha_`

**Permission evaluation:** `PermissionEngine(permissions: Permissions)`
- `evaluate(tool_name: str, args: dict) -> Decision`
- Three-pass rule scan: deny matches first, then allow, then ask
- Then defaults scan: first-match wins
- Global fallback: `Decision.ASK`

#### db.py

**`Database` class with `aiosqlite`:**
- `__init__(path: str)` — store path
- `async initialize()` — create tables + indexes if not exist, set file perms 0600
- `async log_audit(entry: AuditEntry)` — insert audit entry, convert float timestamps to ISO 8601
- `async get_audit_log(limit: int = 100) -> list[AuditEntry]` — query recent entries
- `async insert_pending(request_id, tool_name, args, signature, expires_at)` — insert pending request
- `async get_pending(request_id) -> dict | None` — get single pending request
- `async delete_pending(request_id)` — remove resolved request
- `async cleanup_stale_requests() -> list[dict]` — delete expired pending requests, return them for upstream handling
- `async close()` — close connection

Timestamps stored as ISO 8601 TEXT in SQLite. The `AuditEntry` dataclass uses `float` (epoch) internally; conversion happens at the DB layer boundary.

#### executor.py

```python
TOOL_SERVICE_MAP = {
    "ha_get_state": "homeassistant",
    "ha_get_states": "homeassistant",
    "ha_call_service": "homeassistant",
    "ha_fire_event": "homeassistant",
}
```

**`Executor` class:**
- `__init__(services: dict[str, ServiceHandler])` — registry of service handlers
- `async execute(tool_name: str, args: dict) -> dict` — lookup service, dispatch, return result
- Unknown tool → `ExecutionError("Unknown tool: {tool_name}")`
- Missing service → `ExecutionError("Service not configured: {service_name}")`

**`ExecutionError(Exception)`** — raised on dispatch failures.

#### services/base.py

```python
class ServiceHandler(ABC):
    async def execute(self, tool_name: str, args: dict) -> dict: ...
    async def health_check(self) -> bool: ...
    async def close(self) -> None: ...
```

### Dependencies

- **Existing:** `pyyaml`, `aiosqlite` (from pyproject.toml)
- **New:** None — Spec 1 uses only these two plus stdlib

## Implementation Plan

### Phase 1: Models + Config

1. [ ] Write tests for `models.py` — Decision enum values, ToolRequest/ToolResult/PendingApproval/AuditEntry construction and defaults
2. [ ] Implement `models.py`
3. [ ] Write tests for `config.py` — env var substitution (nested, missing var, non-string values), config validation (missing fields, type coercion, valid/invalid configs), permissions parsing
4. [ ] Implement `config.py`

### Phase 2: Permission Engine

5. [ ] Write tests for `engine.py` — signature building (all 4 HA tools + unknown tool + no-args tool), input validation (forbidden chars, invalid HA identifiers, valid identifiers), permission evaluation (deny wins, allow match, ask match, defaults ordering, fallback to ask)
6. [ ] Implement `engine.py`

### Phase 3: Storage

7. [ ] Write tests for `db.py` — schema creation, audit log insert/query, pending request CRUD, stale cleanup, ISO 8601 conversion
8. [ ] Implement `db.py`

### Phase 4: Executor + Service ABC

9. [ ] Write tests for `executor.py` — dispatch to mock handler, unknown tool rejection, missing service rejection
10. [ ] Implement `executor.py` + `services/base.py`

## Test Plan

### Unit Tests

**test_models.py:**
- [ ] `Decision` enum has values "allow", "deny", "ask"
- [ ] `ToolRequest` defaults: `signature=""`, args is dict
- [ ] `ToolResult` defaults: `data=None`
- [ ] `PendingApproval` sets `created_at` via `time.time()` default factory
- [ ] `AuditEntry` defaults: resolution/resolved_by/resolved_at/execution_result are None, agent_id is "default"

**test_config.py:**
- [ ] `substitute_env_vars` replaces `${VAR}` in strings, dicts, lists
- [ ] `substitute_env_vars` raises `ConfigError` for unset env var
- [ ] `substitute_env_vars` ignores non-string values (int, bool, None)
- [ ] `load_config` returns typed `Config` from valid YAML
- [ ] `load_config` coerces `port` string to int
- [ ] `load_config` coerces `chat_id` string to int
- [ ] `load_config` raises on missing `gateway.host`
- [ ] `load_config` raises on missing `agent.token`
- [ ] `load_config` raises on empty `allowed_users`
- [ ] `load_config` applies default `approval_timeout=900`
- [ ] `load_config` applies default `rate_limit` values
- [ ] `load_permissions` returns typed `Permissions` with defaults and rules
- [ ] `load_permissions` handles empty rules list
- [ ] `load_config` raises on missing config file

**test_engine.py:**
- [ ] `build_signature("ha_call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.bedroom"})` → `"ha_call_service(light.turn_on, light.bedroom)"`
- [ ] `build_signature("ha_get_state", {"entity_id": "sensor.temp"})` → `"ha_get_state(sensor.temp)"`
- [ ] `build_signature("ha_get_states", {})` → `"ha_get_states"`
- [ ] `build_signature("ha_fire_event", {"event_type": "custom_event"})` → `"ha_fire_event(custom_event)"`
- [ ] `build_signature("unknown_tool", {"b": "2", "a": "1"})` → `"unknown_tool(1, 2)"` (sorted keys)
- [ ] `build_signature("no_args_tool", {})` → `"no_args_tool"`
- [ ] `validate_args` raises on `*` in value
- [ ] `validate_args` raises on `[` in value
- [ ] `validate_args` raises on null byte in value
- [ ] `validate_args` raises on control character in value
- [ ] `validate_args` raises on invalid HA identifier (`entity_id` with uppercase)
- [ ] `validate_args` raises on HA identifier with spaces
- [ ] `validate_args` passes valid HA identifiers (`light.bedroom`, `sensor.living_room_temp`)
- [ ] `validate_args` skips non-string values
- [ ] `evaluate` returns DENY when deny rule matches (even if allow rule also matches)
- [ ] `evaluate` returns ALLOW when allow rule matches and no deny rule matches
- [ ] `evaluate` returns ASK when ask rule matches and no deny/allow rules match
- [ ] `evaluate` falls through to defaults when no rules match
- [ ] `evaluate` returns defaults in order (first match wins)
- [ ] `evaluate` returns ASK as global fallback when nothing matches
- [ ] Deny rule overrides more specific allow rule (e.g., `ha_call_service(lock.*)` deny vs `ha_call_service(lock.front_door)` allow)

**test_db.py:**
- [ ] `initialize()` creates tables and indexes
- [ ] `log_audit()` inserts entry with ISO 8601 timestamp
- [ ] `get_audit_log()` returns entries in reverse chronological order
- [ ] `get_audit_log(limit=N)` respects limit
- [ ] `insert_pending()` stores pending request
- [ ] `get_pending()` returns stored request
- [ ] `get_pending()` returns None for missing request_id
- [ ] `delete_pending()` removes request
- [ ] `cleanup_stale_requests()` deletes expired entries and returns them
- [ ] `cleanup_stale_requests()` ignores non-expired entries
- [ ] Database file created with 0600 permissions (Unix only)

**test_executor.py:**
- [ ] `execute()` dispatches `ha_get_state` to homeassistant handler
- [ ] `execute()` dispatches `ha_call_service` to homeassistant handler
- [ ] `execute()` raises `ExecutionError` for unknown tool name
- [ ] `execute()` raises `ExecutionError` when service not configured
- [ ] `execute()` passes correct `tool_name` and `args` to handler
- [ ] `Executor` accepts multiple service handlers

## Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| `${VAR}` where VAR is unset | `ConfigError` raised immediately |
| `${VAR}` in nested dict value | Substituted recursively |
| Config with int port as string after env substitution | Coerced to int |
| `allowed_users: []` (empty) | `ConfigError` — must be non-empty |
| Permission rule with action not in (allow/deny/ask) | `ConfigError` at load time |
| Argument value `light.*` (contains glob char) | `ValueError` — rejected by `validate_args` |
| Entity ID `Light.Bedroom` (uppercase) | `ValueError` — fails HA_IDENTIFIER_RE |
| Empty args dict for `ha_get_states` | Valid — signature is `ha_get_states` (no parens) |
| Tool request for `ha_fire_event` matching deny default | `Decision.DENY` |
| Deny and allow rule both match same signature | Deny wins |
| No rules or defaults match | `Decision.ASK` (global fallback) |
| `cleanup_stale_requests` with no expired entries | Returns empty list, no deletions |
| Audit log query on empty database | Returns empty list |
| Multiple `${VAR}` in single string | All substituted |

## Decision Log

| Decision | Rationale | Date |
|----------|-----------|------|
| config.py parses permissions into typed objects | Engine receives clean typed data; config.py is the single parsing boundary | 2026-02-08 |
| Full Executor + ServiceHandler ABC in Spec 1 | Tests use mock ServiceHandler; Spec 2 just implements HomeAssistantService without changing executor | 2026-02-08 |
| No-arg signatures omit parentheses (`ha_get_states` not `ha_get_states()`) | Matches architecture doc exactly; `ha_get_*` default pattern matches naturally | 2026-02-08 |
| ISO 8601 strings in SQLite, float epoch in dataclasses | Human-readable DB; conversion at DB layer boundary; matches SQL schema in architecture doc | 2026-02-08 |
| Deny always wins regardless of specificity | Security-first: `ha_call_service(lock.*)` deny blocks even if `ha_call_service(lock.front_door)` has an allow rule | Design phase |
| Sorted-key fallback for unknown tool signatures | Deterministic signatures for extensibility without requiring explicit builders for every tool | Design phase |

## Open Questions

_None — all questions resolved during discovery._
