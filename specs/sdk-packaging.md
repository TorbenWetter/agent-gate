# Feature: SDK + Packaging

> Agent SDK client library, Docker deployment, integration tests, and project README.

## Overview

The final phase of agent-gate v1. Ships a thin Python SDK (`AgentGateClient`) that agents use to connect to the gateway, Docker packaging for deployment, end-to-end integration tests covering the full allow/deny/ask flow through real WebSocket transport, and a comprehensive README.

## Requirements

### Functional Requirements

- [ ] FR1: `AgentGateClient` connects to the gateway over WebSocket and authenticates via JSON-RPC `auth` method
  - **Acceptance Criteria:**
  - [ ] FR1-AC1: Client sends `{"jsonrpc":"2.0","method":"auth","params":{"token":"..."},"id":"auth-1"}` as the first message after connecting
  - [ ] FR1-AC2: Client raises `AgentGateError` if authentication fails (invalid token, timeout, or non-auth response)
  - [ ] FR1-AC3: Client supports both `ws://` and `wss://` URLs

- [ ] FR2: `AgentGateClient.tool_request()` sends a tool request and returns the result
  - **Acceptance Criteria:**
  - [ ] FR2-AC1: `tool_request("ha_get_state", entity_id="sensor.temp")` sends properly formatted JSON-RPC with incrementing integer IDs
  - [ ] FR2-AC2: Returns the `result.data` dict on success
  - [ ] FR2-AC3: Raises `AgentGateDenied` (code -32001 or -32003) when denied by user or policy
  - [ ] FR2-AC4: Raises `AgentGateTimeout` (code -32002) when approval times out
  - [ ] FR2-AC5: Raises `AgentGateError` for other error codes (-32700, -32600, -32601, -32004, -32005, -32006)
  - [ ] FR2-AC6: Multiple concurrent `tool_request()` calls resolve independently (pipelining)

- [ ] FR3: `AgentGateClient` supports async context manager
  - **Acceptance Criteria:**
  - [ ] FR3-AC1: `async with AgentGateClient(url, token) as gw:` connects and authenticates on enter
  - [ ] FR3-AC2: Disconnects cleanly on exit (cancels reader task, closes WebSocket)
  - [ ] FR3-AC3: Also usable without context manager via explicit `connect()` / `close()`

- [ ] FR4: Auto-reconnection with exponential backoff
  - **Acceptance Criteria:**
  - [ ] FR4-AC1: On unexpected disconnect, client retries with exponential backoff: 1s, 2s, 4s, 8s... capped at 30s
  - [ ] FR4-AC2: Default is infinite retries; configurable via `max_retries` constructor param (None = infinite)
  - [ ] FR4-AC3: Re-authenticates after reconnecting
  - [ ] FR4-AC4: Automatically calls `get_pending_results` after re-authentication and resolves any pending futures from before disconnect
  - [ ] FR4-AC5: Raises `AgentGateError("Connection lost")` after exhausting max_retries (when finite)
  - [ ] FR4-AC6: Explicit `close()` does not trigger reconnection

- [ ] FR5: `get_pending_results()` retrieves results for requests resolved while disconnected
  - **Acceptance Criteria:**
  - [ ] FR5-AC1: Sends `{"jsonrpc":"2.0","method":"get_pending_results","params":{},"id":N}` and returns the results list
  - [ ] FR5-AC2: Resolves any pending futures whose request_id appears in the returned results
  - [ ] FR5-AC3: Called automatically on reconnect (FR4-AC4)

- [ ] FR6: Typed error hierarchy
  - **Acceptance Criteria:**
  - [ ] FR6-AC1: `AgentGateError(code, message)` is the base exception with `.code` and `.message` attributes
  - [ ] FR6-AC2: `AgentGateDenied` extends `AgentGateError` for denial codes (-32001, -32003)
  - [ ] FR6-AC3: `AgentGateTimeout` extends `AgentGateError` for timeout code (-32002)
  - [ ] FR6-AC4: `AgentGateConnectionError` extends `AgentGateError` for connection/auth failures

- [ ] FR7: Public exports from `agent_gate` package
  - **Acceptance Criteria:**
  - [ ] FR7-AC1: `from agent_gate import AgentGateClient` works
  - [ ] FR7-AC2: `from agent_gate import AgentGateError, AgentGateDenied, AgentGateTimeout, AgentGateConnectionError` works

- [ ] FR8: Dockerfile builds a working image
  - **Acceptance Criteria:**
  - [ ] FR8-AC1: `docker build -t agent-gate .` succeeds with the `python:3.12-slim` base image
  - [ ] FR8-AC2: Image runs `agent-gate` as the default command
  - [ ] FR8-AC3: `/app/data` is a declared volume for SQLite + PicklePersistence
  - [ ] FR8-AC4: Port 8443 is exposed

- [ ] FR9: docker-compose.yml orchestrates the gateway
  - **Acceptance Criteria:**
  - [ ] FR9-AC1: Mounts `config.yaml`, `permissions.yaml` (read-only), `data/` (read-write), and `certs/` (read-only)
  - [ ] FR9-AC2: Uses `env_file: .env` for secret substitution
  - [ ] FR9-AC3: Sets `restart: unless-stopped`
  - [ ] FR9-AC4: Maps port 8443

- [ ] FR10: Integration test covers the full allow/deny/ask flow through real WebSocket
  - **Acceptance Criteria:**
  - [ ] FR10-AC1: Test starts a real WebSocket server with mocked HA and Telegram services
  - [ ] FR10-AC2: Client SDK connects, authenticates, and sends tool requests
  - [ ] FR10-AC3: Tests an auto-allowed request (policy → allow → executed)
  - [ ] FR10-AC4: Tests a denied request (policy → deny → error)
  - [ ] FR10-AC5: Tests an ask request with simulated approval (policy → ask → human approves → executed)
  - [ ] FR10-AC6: Tests an ask request with simulated denial (policy → ask → human denies → error)
  - [ ] FR10-AC7: Tests offline retrieval: client disconnects while approval is pending, approval resolves, client reconnects and retrieves result via `get_pending_results`

- [ ] FR11: README.md with project overview, quick start, SDK usage, Docker deployment, and configuration reference
  - **Acceptance Criteria:**
  - [ ] FR11-AC1: Includes project description, architecture diagram (text), and security model explanation
  - [ ] FR11-AC2: Quick start section with installation, config setup, and running the gateway
  - [ ] FR11-AC3: SDK usage examples (context manager, tool_request, error handling)
  - [ ] FR11-AC4: Docker deployment section referencing docker-compose.yml
  - [ ] FR11-AC5: Configuration reference covering config.yaml and permissions.yaml
  - [ ] FR11-AC6: JSON-RPC protocol reference for non-Python agents

### Non-Functional Requirements

- [ ] NFR1: SDK has zero extra dependencies beyond what agent-gate already requires (websockets + stdlib)
  - **Acceptance Criteria:**
  - [ ] NFR1-AC1: `client.py` imports only from `websockets`, `json`, `asyncio`, and stdlib modules

- [ ] NFR2: Docker image is minimal
  - **Acceptance Criteria:**
  - [ ] NFR2-AC1: Based on `python:3.12-slim`
  - [ ] NFR2-AC2: `.dockerignore` excludes tests, docs, .git, __pycache__, .env

- [ ] NFR3: All new code passes `ruff check` and `ruff format --check`
  - **Acceptance Criteria:**
  - [ ] NFR3-AC1: Zero ruff lint violations
  - [ ] NFR3-AC2: Zero ruff format violations

## User Experience

### User Flows

1. **Agent developer integrating with agent-gate:**
   ```python
   from agent_gate import AgentGateClient, AgentGateDenied

   async with AgentGateClient("wss://gateway:8443", token="secret") as gw:
       temp = await gw.tool_request("ha_get_state", entity_id="sensor.temp")
       print(temp)  # {"entity_id": "sensor.temp", "state": "21.3", ...}

       try:
           await gw.tool_request("ha_call_service", domain="light", service="turn_on", entity_id="light.bedroom")
       except AgentGateDenied:
           print("Request was denied")
   ```

2. **Deploying with Docker:**
   ```bash
   cp config.example.yaml config.yaml
   cp permissions.example.yaml permissions.yaml
   # Edit config.yaml and permissions.yaml, create .env with secrets
   docker compose up -d
   ```

### Edge Cases

| Scenario | Expected Behavior |
| --- | --- |
| Gateway unreachable on connect | `AgentGateConnectionError` raised after backoff retries exhausted |
| Auth token invalid | `AgentGateError(-32005, "Invalid token")` raised |
| Gateway disconnects mid-request | Auto-reconnect, re-auth, retrieve pending results |
| Multiple concurrent tool_request calls | Each resolves independently via request ID matching |
| `close()` called during reconnect backoff | Stops reconnection, exits cleanly |
| `tool_request()` called while disconnected/reconnecting | Waits for reconnection, then sends; or raises if max_retries exhausted |

## Technical Design

### Affected Components

- `src/agent_gate/client.py` — New: AgentGateClient, error classes, read loop, reconnection logic
- `src/agent_gate/__init__.py` — Modified: add public exports (AgentGateClient, error classes)
- `Dockerfile` — New: multi-stage-free slim build
- `.dockerignore` — New: exclude non-essential files
- `docker-compose.yml` — New: service orchestration
- `tests/test_client.py` — New: unit tests for client SDK
- `tests/test_integration.py` — New: end-to-end integration tests
- `README.md` — New: project documentation

### Data Model

No new data models. Client uses the existing JSON-RPC 2.0 protocol defined in Spec 2.

### Client SDK Architecture

```
AgentGateClient
├── connect()           → websockets.connect() + _authenticate() + start _read_loop
├── tool_request()      → send JSON-RPC, create Future, await result
├── get_pending_results() → send JSON-RPC, resolve offline futures
├── close()             → cancel reader, close WS, set _closed flag
├── _read_loop()        → async for msg in ws: dispatch to pending futures
├── _authenticate()     → send auth, await response, raise on error
└── _reconnect()        → exponential backoff loop, re-auth, poll offline results
```

### Dependencies

- Existing: `websockets` (already in pyproject.toml)
- New: None

## Implementation Plan

### Task Decomposition

#### T1: Client SDK — Error Classes + Core Protocol (no reconnection)

**Files:** `src/agent_gate/client.py`, `tests/test_client.py`

**Scope:**
- Error class hierarchy: `AgentGateError`, `AgentGateDenied`, `AgentGateTimeout`, `AgentGateConnectionError`
- `AgentGateClient.__init__(url, token, max_retries=None)`
- `connect()` — WebSocket connect + authenticate
- `close()` — Cancel reader, close WebSocket
- `__aenter__` / `__aexit__` — Context manager
- `tool_request(tool, **args)` — Send JSON-RPC, create future, return result
- `get_pending_results()` — Send JSON-RPC, resolve pending futures
- `_read_loop()` — Background task dispatching responses to futures
- `_authenticate()` — Send auth message, validate response
- `_next_id()` — Incrementing integer request IDs

**Tests (unit, mocked WebSocket):**
- Error class instantiation and inheritance
- Successful connect + auth
- Auth failure (wrong token, timeout)
- tool_request success (auto-allowed)
- tool_request denied (policy, user)
- tool_request timeout
- tool_request execution error
- Multiple concurrent tool_requests (pipelining)
- Context manager enter/exit
- close() cancels reader task
- get_pending_results with results
- get_pending_results with empty results

**Acceptance criteria:** FR1, FR2, FR3, FR5, FR6

#### T2: Client SDK — Auto-Reconnection

**Files:** `src/agent_gate/client.py`, `tests/test_client.py`

**Depends on:** T1

**Scope:**
- `_reconnect()` — Exponential backoff: 1s, 2s, 4s... capped at 30s
- Infinite retries by default, `max_retries` param
- Re-authenticate after reconnecting
- Auto-call `get_pending_results` after re-auth to resolve pending futures
- `close()` sets `_closed` flag to prevent reconnection
- `tool_request()` during disconnected state waits for reconnect

**Tests (unit, mocked WebSocket):**
- Auto-reconnect on unexpected disconnect
- Exponential backoff timing (1s, 2s, 4s, capped at 30s)
- Max retries exhausted raises AgentGateConnectionError (when finite)
- Infinite retries (default) keeps trying
- Re-auth after reconnect
- Pending results auto-fetched on reconnect
- close() stops reconnection
- Pending tool_request resolved after reconnect + offline retrieval

**Acceptance criteria:** FR4

#### T3: Public Exports + Docker

**Files:** `src/agent_gate/__init__.py`, `Dockerfile`, `.dockerignore`, `docker-compose.yml`

**Depends on:** T1

**Scope:**
- Update `__init__.py` with public exports
- `Dockerfile`: python:3.12-slim, WORKDIR /app, install package, VOLUME /app/data, EXPOSE 8443, CMD agent-gate
- `.dockerignore`: tests/, docs/, .git/, __pycache__/, *.pyc, .env, .pytest_cache/, .ruff_cache/
- `docker-compose.yml`: volumes, env_file, ports, restart policy

**Tests:**
- FR7: import checks (test that `from agent_gate import AgentGateClient` works)
- FR8/FR9: Dockerfile syntax validation (docker build --check if available, or just syntax review)

**Acceptance criteria:** FR7, FR8, FR9, NFR2

#### T4: Integration Tests

**Files:** `tests/test_integration.py`

**Depends on:** T1, T2

**Scope:**
- End-to-end tests using real WebSocket server (in-process)
- Mocked HA service (returns canned responses)
- Mocked Telegram adapter (auto-approves/denies on demand)
- Test flows: allow, deny, ask+approve, ask+deny, offline retrieval

**Tests:**
- Auto-allowed request through real WS
- Policy-denied request through real WS
- Ask + simulated human approval through real WS
- Ask + simulated human denial through real WS
- Offline retrieval: disconnect while pending, resolve, reconnect, retrieve

**Acceptance criteria:** FR10

#### T5: README.md

**Files:** `README.md`

**Depends on:** T1, T3

**Scope:**
- Project overview + security model
- Architecture diagram (text-based, from spec)
- Quick start (install, configure, run)
- SDK usage examples
- Docker deployment
- Configuration reference (config.yaml, permissions.yaml)
- JSON-RPC protocol reference
- Development section (tests, linting)

**Acceptance criteria:** FR11

### Dependency Graph

```
T1 (Client core)  ──→  T2 (Reconnection)  ──→  T4 (Integration)
      │
      └──→  T3 (Exports + Docker)  ──→  T5 (README)
```

**Parallel opportunities:**
- Wave 1: T1
- Wave 2: T2 + T3 (parallel)
- Wave 3: T4 + T5 (parallel)

## Test Plan

### Unit Tests (T1 + T2)

- [ ] Error class hierarchy and attributes
- [ ] Connect + authenticate (success)
- [ ] Connect + auth failure (wrong token)
- [ ] Connect + auth timeout
- [ ] tool_request success path
- [ ] tool_request denied (policy -32003)
- [ ] tool_request denied (user -32001)
- [ ] tool_request timeout (-32002)
- [ ] tool_request execution error (-32004)
- [ ] tool_request rate limited (-32006)
- [ ] Multiple concurrent tool_requests
- [ ] Context manager lifecycle
- [ ] close() cancels reader
- [ ] get_pending_results resolves futures
- [ ] Auto-reconnect with exponential backoff
- [ ] Max retries exhausted
- [ ] Re-auth on reconnect
- [ ] Auto-fetch pending results on reconnect
- [ ] close() prevents reconnect
- [ ] tool_request during reconnect waits

### Import Tests (T3)

- [ ] `from agent_gate import AgentGateClient` works
- [ ] `from agent_gate import AgentGateError, AgentGateDenied, AgentGateTimeout, AgentGateConnectionError` works

### Integration Tests (T4)

- [ ] Auto-allowed request (real WS, mocked HA)
- [ ] Policy-denied request (real WS)
- [ ] Ask + approval (real WS, mocked Telegram)
- [ ] Ask + denial (real WS, mocked Telegram)
- [ ] Offline retrieval (disconnect, resolve, reconnect, retrieve)

## Open Questions

_None — all questions resolved during discovery._

## Decision Log

| Decision | Rationale | Date |
| --- | --- | --- |
| Exponential backoff with infinite default | Agents on edge devices need resilience; callers can limit with max_retries | 2026-02-08 |
| Zero extra dependencies for SDK | Same package, no dependency bloat; websockets + stdlib sufficient | 2026-02-08 |
| Auto-fetch pending results on reconnect | Seamless experience — pending futures from before disconnect resolve automatically | 2026-02-08 |
| Include README in Spec 3 | Project documentation makes sense alongside SDK and Docker packaging | 2026-02-08 |
| Full e2e + offline retrieval for integration tests | Validates the complete security model and offline resilience end-to-end | 2026-02-08 |
