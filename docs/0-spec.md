---
title: "agent-gate - Project Specification"
created: 2026-02-07T22:50:00Z
tags: [openclaw, agent-gate, spec]
generated_by: Claude Code
---

# agent-gate

**An execution gateway for AI agents on untrusted devices.**

Agents request. Policies decide. Humans approve. The gateway executes.

## Problem

AI agents (OpenClaw, Moltbot, etc.) running on untrusted devices (Raspberry Pi, edge hardware) need access to personal services (Home Assistant, Gmail, calendars). But:

- The device has full shell access — any credential stored on it can be extracted
- Prompt injection from untrusted content (Moltbook, web) can compromise the agent
- Services like IMAP and Home Assistant lack fine-grained permission scoping

**No existing solution** combines credential isolation, human-in-the-loop approval, and gateway-side action execution. Existing projects (Latch, Agent Consent Protocol, amitpaz1/agentgate) are approval-only — they gate the decision but the agent still holds credentials and executes actions.

## Core Idea

The gateway sits on a **trusted device** (home server, NAS, cloud). The agent sits on an **untrusted device** (Pi). The agent never sees service credentials. When the agent wants to perform an action:

1. Agent sends a tool request to the gateway via WebSocket
2. Gateway evaluates the request against permission policies
3. If allowed: gateway executes immediately using its own credentials
4. If denied: gateway rejects immediately
5. If ask: gateway sends a Telegram approval message with inline buttons
6. Human approves/denies via Telegram
7. Gateway executes (or rejects) and returns the result to the agent

## Architecture

```
Untrusted Device (Pi)              Trusted Device (Gateway)
┌──────────────┐                   ┌──────────────────────────────┐
│              │                   │  agent-gate                  │
│  AI Agent    │                   │  ┌────────────────────────┐  │
│  (OpenClaw)  │── WebSocket ────→ │  │  Permission Engine     │  │
│              │                   │  │  deny → allow → ask    │  │
│  Holds:      │                   │  └──────────┬─────────────┘  │
│  - Conv bot  │                   │             │                 │
│    token     │                   │  ┌──────────▼─────────────┐  │
│  - LLM key   │                   │  │  Messenger Adapter     │  │
│              │← result ──────── │  │  (Telegram, Slack, ...) │  │
│              │                   │  └──────────┬─────────────┘  │
└──────────────┘                   │             │                 │
                                   │  ┌──────────▼─────────────┐  │
      User ◄── Telegram ───────── │  │  Action Executor       │  │
                                   │  │  (HA, Gmail, plugins)  │  │ ── credentials ──→ Services
                                   │  └────────────────────────┘  │
                                   │                               │
                                   │  Holds: HA token, Guardian bot│
                                   │  token, TLS cert, permission DB│
                                   └──────────────────────────────┘
```

## Key Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | Best library support, most accessible for contributors |
| Agent integration | Agent-agnostic from day 1 | Generic WebSocket/HTTP API, any agent can integrate |
| Messenger layer | Full abstraction from start | MessengerAdapter interface, Telegram is first impl |
| Telegram architecture | Two-bot (Guardian) | Cryptographic isolation, no OpenClaw core modifications |
| Agent auth | Bearer token over WSS | WSS (TLS) required by default; mTLS in v2 |
| Transport | WSS required | Plaintext `ws://` only with explicit `--insecure` flag |
| Action execution | Gateway executes | Gateway holds credentials AND executes approved actions |
| Configuration | YAML files | Simple, version-controllable, human-readable |
| Tool system | MCP + native Python plugins | MCP for ecosystem compat, plugins for simple use cases |
| Distribution | pip + Docker | `pip install agent-gate` + Docker image (import as `agent_gate`) |
| Naming | agent-gate | Hyphenated everywhere — available on PyPI, clean and consistent |
| WebSocket protocol | JSON-RPC 2.0 | Standard, well-tooled, bidirectional, familiar to most developers |
| Approval timeout | 15 minutes (configurable) | Balanced — not too tight for AFK, not too long for stale requests |
| Error reporting | Agent chat only | Gateway errors flow back to the agent, which reports to the user naturally |
| Agent SDK | Ship in v1 + protocol docs | Thin Python client (`AgentGateClient`) + full JSON-RPC 2.0 docs for non-Python agents |
| Multi-agent | Single agent in v1 | One connection at a time; multi-agent deferred to v2 |
| Health checks | Non-blocking startup checks | Verify service connectivity on boot (warn-only), also report errors at request time |
| Rate limiting | Basic v1 limits | Max 10 pending approvals, max 60 auto-allowed/min, prevent Telegram API abuse |
| Rule precedence | Deny always wins | deny rules → allow rules → ask rules → defaults; specificity does NOT override action priority |
| Storage | SQLite | Sufficient for personal/small-team use |

## v1 Scope (Weekend 1 — "Hello, Light")

The minimum viable product that demonstrates the full security model end-to-end:

### In scope

- [ ] Gateway skeleton (asyncio event loop, WebSocket server)
- [ ] Guardian Telegram bot (inline keyboard approvals)
- [ ] Permission engine (deny → allow → ask → default, glob patterns with `fnmatch`)
- [ ] Home Assistant integration (read sensor states, turn on/off devices)
- [ ] YAML configuration (`config.yaml`, `permissions.yaml`)
- [ ] Audit logging (SQLite)
- [ ] Agent authentication (bearer token)
- [ ] Thin Python SDK (`AgentGateClient` — connect, tool_request, close)
- [ ] JSON-RPC 2.0 protocol documentation
- [ ] Non-blocking startup health checks (service connectivity)
- [ ] Basic rate limiting (max pending approvals, max requests/min)
- [ ] Input validation (entity_id format, argument sanitization)
- [ ] PTB PicklePersistence (survive gateway restarts)
- [ ] Docker deployment (`Dockerfile`, `docker-compose.yml`)

### Out of scope for v1

- Gmail integration
- MCP proxy support
- Native plugin system (HA is hardcoded in v1)
- Web dashboard
- mTLS authentication
- Messenger adapters other than Telegram
- Multiple simultaneous agent connections
- "Always Allow" remembered choices (permissions are static YAML in v1)

### v1 Demo Scenario

```
User (in Telegram group): "What's the temperature in the living room?"
OpenClaw (conv bot): "Let me check..."
  → Agent calls gateway: tool_request("ha_get_state", {"entity_id": "sensor.living_room_temp"})
  → Gateway: policy matches "ha_get_*" → allow
  → Gateway: executes GET /api/states/sensor.living_room_temp with its HA token
  → Gateway: returns result to agent
OpenClaw: "The living room is at 21.3°C."

User: "Turn on the bedroom light."
OpenClaw: "I'll turn on the bedroom light — requesting approval..."
  → Agent calls gateway: tool_request("ha_call_service", {"domain": "light", "service": "turn_on", "entity_id": "light.bedroom"})
  → Gateway: policy matches "ha_call_service*" → ask
  → Guardian bot sends inline keyboard:
      🔒 Permission Request
      Action: ha_call_service(light.turn_on, light.bedroom)
      [✓ Allow]  [✗ Deny]
  → User taps [✓ Allow]
  → Gateway: executes POST /api/services/light/turn_on with its HA token
  → Gateway: returns result to agent
  → Guardian bot edits message: "✅ Approved: ha_call_service(light.turn_on)"
OpenClaw: "Done — bedroom light is on."
```

## Messenger Adapter Interface

```python
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

@dataclass
class ApprovalRequest:
    request_id: str
    tool_name: str
    args: dict
    description: str  # human-readable summary

@dataclass
class ApprovalChoice:
    """Represents one button in the approval message."""
    label: str        # "✓ Allow", "✗ Deny"
    action: str       # "allow", "deny" (v2: "always_allow")

@dataclass
class ApprovalResult:
    request_id: str
    action: str       # "allow", "deny"
    user_id: str      # messenger-specific user identifier
    timestamp: float

class MessengerAdapter(ABC):
    """Interface for messenger integrations."""

    @abstractmethod
    async def send_approval(
        self,
        request: ApprovalRequest,
        choices: list[ApprovalChoice],
    ) -> str:
        """Send an approval message. Returns a message_id for later editing."""
        ...

    @abstractmethod
    async def update_approval(
        self,
        message_id: str,
        status: str,  # "approved", "denied", "expired"
        detail: str,
    ) -> None:
        """Edit the approval message to reflect the decision."""
        ...

    @abstractmethod
    async def on_approval_callback(
        self,
        callback: Callable[[ApprovalResult], Awaitable[None]],
    ) -> None:
        """Register a callback for when the user taps a button."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start listening for callbacks (webhook or polling)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down."""
        ...
```

## Permission Engine

```yaml
# permissions.yaml
# IMPORTANT: defaults are evaluated in order — put specific patterns before "*"
defaults:
  - pattern: "ha_get_*"
    action: allow
  - pattern: "ha_call_service*"
    action: ask
  - pattern: "*"
    action: ask

rules:
  - pattern: "ha_call_service(light.*)"
    action: ask
    description: "Light control requires approval"

  - pattern: "ha_call_service(lock.*)"
    action: deny
    description: "Lock control is always denied"
```

**Evaluation order:** deny rules → allow rules → ask rules → defaults (first match) → global fallback (ask).

Deny always wins: if any deny rule matches, the request is denied regardless of more-specific allow/ask rules. This is a deliberate security-first design — use deny rules sparingly and precisely.

**Signature format:** `tool_name(arg1, arg2, ...)` — built from explicit per-tool signature builders (not raw dict iteration). See [1-architecture.md](./1-architecture.md#2-enginepy--permission-engine) for details.

**Argument sanitization:** Argument values are validated before inclusion in signature strings. Glob metacharacters (`*`, `?`, `[`, `]`), parentheses, commas, null bytes, and control characters in argument values cause the request to be rejected (error -32600). HA entity IDs are further validated against `^[a-z_]+\.[a-z0-9_]+$`.

## Configuration

```yaml
# config.yaml
gateway:
  host: "0.0.0.0"
  port: 8443
  tls:
    cert: "/path/to/cert.pem"    # required unless --insecure
    key: "/path/to/key.pem"

agent:
  token: "${AGENT_TOKEN}"

messenger:
  type: "telegram"
  telegram:
    token: "${GUARDIAN_BOT_TOKEN}"
    chat_id: 123456789              # integer — Telegram chat ID (negative for groups)
    allowed_users: [123456789]      # required — Telegram user IDs who can approve

services:
  homeassistant:
    url: "http://homeassistant.local:8123"
    token: "${HA_TOKEN}"

storage:
  type: "sqlite"
  path: "./data/agent-gate.db"
```

## Project Structure

```
agent-gate/
├── src/
│   └── agent_gate/
│       ├── __init__.py
│       ├── __main__.py          # CLI entrypoint
│       ├── config.py            # YAML config loading + env var substitution
│       ├── server.py            # WebSocket server (agent connections)
│       ├── engine.py            # Permission engine (glob matching)
│       ├── executor.py          # Action execution dispatcher
│       ├── models.py            # Dataclasses (requests, rules, audit entries)
│       ├── db.py                # SQLite schema + queries (aiosqlite)
│       ├── client.py            # Thin SDK: AgentGateClient (for agent-side integration)
│       ├── messenger/
│       │   ├── __init__.py
│       │   ├── base.py          # MessengerAdapter ABC
│       │   └── telegram.py      # Telegram Guardian bot implementation
│       └── services/
│           ├── __init__.py
│           ├── base.py          # ServiceHandler ABC
│           └── homeassistant.py # HA REST API client
├── tests/
│   ├── test_engine.py
│   ├── test_server.py
│   └── test_telegram.py
├── config.example.yaml
├── permissions.example.yaml
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── README.md
└── LICENSE                      # MIT
```

## WebSocket Protocol (JSON-RPC 2.0)

Agent-to-gateway communication uses [JSON-RPC 2.0](https://www.jsonrpc.org/specification) over WebSocket.

```jsonc
// Agent → Gateway: authenticate (must be first message, within 10s of connect)
{
  "jsonrpc": "2.0",
  "method": "auth",
  "params": {"token": "..."},
  "id": "auth-1"
}

// Gateway → Agent: auth success
{
  "jsonrpc": "2.0",
  "result": {"status": "authenticated"},
  "id": "auth-1"
}

// Agent → Gateway: tool request
{
  "jsonrpc": "2.0",
  "method": "tool_request",
  "params": {
    "tool": "ha_call_service",
    "args": {"domain": "light", "service": "turn_on", "entity_id": "light.bedroom"}
  },
  "id": "req-001"
}

// Gateway → Agent: immediate result (allowed or denied)
{
  "jsonrpc": "2.0",
  "result": {
    "status": "executed",  // "executed" or "denied"
    "data": { ... }        // service response (if executed)
  },
  "id": "req-001"
}

// Gateway → Agent: deferred result (after human approval)
{
  "jsonrpc": "2.0",
  "result": {
    "status": "executed",
    "data": { ... }
  },
  "id": "req-001"
}

// Gateway → Agent: approval denied or timed out
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32001,
    "message": "Approval denied by user"
  },
  "id": "req-001"
}

// Gateway → Agent: approval timeout (15 min default)
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32002,
    "message": "Approval timed out"
  },
  "id": "req-001"
}
```

## Agent SDK

The SDK ships as part of the same package (`pip install agent-gate`). Agents integrate in a few lines:

```python
from agent_gate import AgentGateClient

async with AgentGateClient("wss://gateway:8443", token="...") as gw:
    # Auto-allowed by policy — returns immediately
    temp = await gw.tool_request("ha_get_state", entity_id="sensor.living_room_temp")

    # Requires human approval — blocks until approved/denied/timeout
    await gw.tool_request("ha_call_service", domain="light", service="turn_on", entity_id="light.bedroom")
```

The SDK handles WebSocket connection management, JSON-RPC serialization, reconnection with backoff, and typed responses. For non-Python agents, the JSON-RPC 2.0 protocol is fully documented above.

## Naming & Distribution

| Surface | Name | Notes |
|---|---|---|
| Brand / project | agent-gate | Used in docs, README, conversation |
| GitHub repo | agent-gate | `github.com/<user>/agent-gate` |
| PyPI package | agent-gate | `pip install agent-gate` |
| Python import | agent_gate | `import agent_gate` (PEP 8: hyphens become underscores) |
| Docker image | agent-gate | `docker pull ghcr.io/<user>/agent-gate` |
| CLI command | agent-gate | `python -m agent_gate` or `agent-gate` entrypoint |

## Open Questions (v2+)

1. **"Always Allow" UX**: How should the button/glob generation work? Exact match (`turn_on(light.kitchen)`) vs prefix glob (`turn_on(light.*)`) vs both as separate buttons?
2. **Token rotation**: Short-lived JWT tokens exchanged via challenge-response, with the long-lived secret only for initial handshake?
3. **Multi-agent support**: Per-agent tokens, per-agent permission scopes, agent identity in audit log?
4. **Rate limiting refinement**: Per-tool-type limits, adaptive rate limiting based on denial history, circuit breaker pattern?
