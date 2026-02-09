# agentpass

An execution gateway for AI agents on untrusted devices. Agents request, policies decide, humans approve, the gateway executes.

## Tech Stack

- Python 3.12+
- `websockets` >= 14.0 — WebSocket server (agent connections)
- `python-telegram-bot[callback-data]` >= 21.0 — Telegram Guardian bot
- `aiosqlite` >= 0.20.0 — async SQLite
- `aiohttp` >= 3.10.0 — HTTP client for services
- `pyyaml` >= 6.0 — config loading

## Project Structure

```
agentpass/
├── src/agentpass/
│   ├── __init__.py
│   ├── __main__.py           # CLI entrypoint + orchestration
│   ├── cli.py                # Client-side subcommands (request, tools, pending)
│   ├── config.py             # YAML loading + env var substitution
│   ├── models.py             # Dataclasses (Decision, ToolRequest, etc.)
│   ├── engine.py             # Permission engine (fnmatch, signature builders)
│   ├── executor.py           # Action dispatch (tool → service via registry)
│   ├── registry.py           # ToolRegistry (tool name → service mapping)
│   ├── server.py             # WebSocket server + pending request mgmt
│   ├── db.py                 # SQLite (audit_log, pending_requests)
│   ├── client.py             # Agent SDK (AgentPassClient)
│   ├── messenger/
│   │   ├── base.py           # MessengerAdapter ABC
│   │   └── telegram.py       # Telegram Guardian bot (PTB v21)
│   └── services/
│       ├── base.py           # ServiceHandler ABC
│       └── http.py           # Generic HTTP service (any API via YAML)
├── tools/
│   └── homeassistant.yaml    # HA tool definitions
├── tests/                    # 377 tests across 17 files
├── specs/                    # Feature specs (dated)
├── config.example.yaml
├── permissions.example.yaml
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
```

## Key Patterns

### Extensible Tool System

Tools are defined in YAML files, not Python code. Each service gets a YAML file with tool definitions:

```yaml
tools:
  ha_get_state:
    signature: "{entity_id}"
    args:
      entity_id:
        { required: true, validate: "^[a-z_][a-z0-9_]*(\\.[a-z0-9_]+)?$" }
    request:
      method: GET
      path: "/api/states/{entity_id}"
```

`ToolRegistry` maps tool names → services. `GenericHTTPService` (`services/http.py`) handles execution for any YAML-defined tool. For non-HTTP protocols, use `handler: python` with a `ServiceHandler` subclass.

### PTB Event Loop

PTB v21's `run_polling()` creates its own event loop — DO NOT use it. Use manual lifecycle:

```python
async with ptb_app:
    await ptb_app.start()
    await ptb_app.updater.start_polling()
    # ... run websockets.serve() here ...
    await ptb_app.updater.stop()
    await ptb_app.stop()
```

### Permission Engine Precedence

Deny always wins: `deny rules → allow rules → ask rules → defaults (first match) → fallback (ask)`

### Signature Builders

Signatures are built from YAML `signature` templates (e.g., `"{domain}.{service}, {entity_id}"`). Fallback for tools without a template: sorted keys for determinism.

### Input Validation

Reject argument values containing: `* ? [ ] ( ) , \x00-\x1f`
Per-tool validation via `validate` regex in YAML. HA identifiers: `^[a-z_][a-z0-9_]*(\.[a-z0-9_]+)?$`

### Transport Security

WSS (TLS) required by default. Plaintext `ws://` only with explicit `--insecure` flag.

## Linting & Formatting

- **Ruff** — single tool for linting + formatting, configured in `pyproject.toml`
- Lint: `ruff check src/ tests/`
- Format: `ruff format --check src/ tests/`
- Pre-commit hook runs: ruff format → ruff check → pytest

## Testing

- `pytest` + `pytest-asyncio` (asyncio_mode = "auto")
- Mock external services: WebSocket, Telegram Bot API, HTTP APIs
- Unit tests per module, integration test for full flow
- 377 tests across 17 files
