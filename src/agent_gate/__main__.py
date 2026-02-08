"""CLI entrypoint and orchestration for agent-gate."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import ssl
import sys
from pathlib import Path

import websockets
import websockets.asyncio.server

from agent_gate.config import ConfigError, load_config, load_permissions
from agent_gate.db import Database
from agent_gate.engine import PermissionEngine
from agent_gate.executor import Executor
from agent_gate.messenger.telegram import TelegramAdapter
from agent_gate.server import GatewayServer
from agent_gate.services.homeassistant import HomeAssistantService

logger = logging.getLogger("agent_gate")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Supports:
        --insecure       Allow plaintext WS (no TLS)
        --config PATH    Config file path (default: config.yaml)
        --permissions PATH  Permissions file path (default: permissions.yaml)
    """
    parser = argparse.ArgumentParser(description="agent-gate: execution gateway for AI agents")
    parser.add_argument("--insecure", action="store_true", help="Allow plaintext WS (no TLS)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--permissions", default="permissions.yaml", help="Permissions file path")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    """Main async entrypoint -- orchestrates all components."""
    # 1. Load config
    config = load_config(args.config)
    permissions = load_permissions(args.permissions)

    # 2. TLS check
    if not args.insecure and config.gateway.tls is None:
        logger.error("TLS not configured. Use --insecure to allow plaintext WS.")
        sys.exit(1)

    # 3. Signal handling
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    # 4. Initialize database
    db = Database(config.storage.path)
    await db.initialize()
    await db.cleanup_stale_requests()

    # 5. Initialize services + health checks
    ha_config = config.services["homeassistant"]
    ha = HomeAssistantService(ha_config)
    if not await ha.health_check():
        logger.warning("Home Assistant unreachable — continuing anyway")

    executor = Executor({"homeassistant": ha})

    # 6. Initialize permission engine
    engine = PermissionEngine(permissions)

    # 7. Initialize Telegram adapter
    storage_dir = Path(config.storage.path).parent
    persistence_path = str(storage_dir / "callback_data.pickle")
    telegram = TelegramAdapter(config.messenger.telegram, persistence_path=persistence_path)

    # 8. Initialize gateway server
    gateway = GatewayServer(
        agent_token=config.agent.token,
        engine=engine,
        executor=executor,
        messenger=telegram,
        db=db,
        approval_timeout=config.approval_timeout,
        rate_limit_config=config.rate_limit,
    )

    # Wire approval callback
    await telegram.on_approval_callback(gateway.resolve_approval)

    # 9. PTB manual lifecycle -- NOT run_polling()
    ptb_app = telegram.application
    async with ptb_app:
        await ptb_app.start()
        await ptb_app.updater.start_polling()

        # 10. SSL context
        ssl_ctx = None
        if config.gateway.tls:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(config.gateway.tls.cert, config.gateway.tls.key)

        # 11. Start WebSocket server
        async with websockets.asyncio.server.serve(
            gateway.handle_connection,
            config.gateway.host,
            config.gateway.port,
            ssl=ssl_ctx,
        ):
            proto = "wss" if ssl_ctx else "ws"
            logger.info(
                "agent-gate ready on %s://%s:%d",
                proto,
                config.gateway.host,
                config.gateway.port,
            )
            await stop_event.wait()

        # 12. Graceful shutdown
        logger.info("Shutting down...")
        await gateway.resolve_all_pending("gateway_shutdown")
        await telegram.stop()
        await ptb_app.updater.stop()
        await ptb_app.stop()

    await ha.close()
    await db.close()
    logger.info("agent-gate stopped")


def main(argv: list[str] | None = None) -> None:
    """Synchronous entrypoint for the CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    args = parse_args(argv)
    try:
        asyncio.run(run(args))
    except ConfigError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
