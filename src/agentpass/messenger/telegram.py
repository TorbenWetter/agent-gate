"""Telegram Guardian bot adapter using python-telegram-bot (PTB) v21 with manual lifecycle."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    InvalidCallbackData,
    PicklePersistence,
)

from agentpass.config import TelegramConfig
from agentpass.messenger.base import (
    ApprovalChoice,
    ApprovalRequest,
    ApprovalResult,
    MessengerAdapter,
)

logger = logging.getLogger(__name__)


class TelegramAdapter(MessengerAdapter):
    """Telegram-based guardian approval bot.

    Uses PTB v21 manual lifecycle (NOT run_polling) so the event loop is shared
    with the WebSocket server in __main__.py.

    Configuration:
        - arbitrary_callback_data=True with PicklePersistence so callback data
          (Python dicts) survives restarts.
        - Each pending approval gets an asyncio timeout task.
        - asyncio.Lock ensures race-safe resolution between user callback and timeout.
    """

    def __init__(
        self,
        config: TelegramConfig,
        *,
        persistence_path: str | None = None,
    ) -> None:
        self._config = config
        self._callback: Callable[[ApprovalResult], Awaitable[None]] | None = None
        self._pending: dict[str, asyncio.Task] = {}  # request_id -> timeout task
        self._resolve_lock = asyncio.Lock()
        self._resolved: set[str] = set()  # already-resolved request_ids

        # Persistence for arbitrary callback data survival across restarts
        pp = persistence_path or "data/callback_data.pickle"
        Path(pp).parent.mkdir(parents=True, exist_ok=True)
        persistence = PicklePersistence(filepath=pp)

        self._app = (
            Application.builder()
            .token(config.token)
            .persistence(persistence)
            .arbitrary_callback_data(True)
            .build()
        )

        # Major 4: InvalidCallbackData handler MUST be registered first so it catches
        # stale/expired callback data before the valid handler tries to access dict keys.
        self._app.add_handler(
            CallbackQueryHandler(self._handle_invalid_callback, pattern=InvalidCallbackData)
        )
        # Valid callback data handler (dict payloads)
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

    @property
    def application(self) -> Application:
        """Expose the PTB Application for manual lifecycle management."""
        return self._app

    # ------------------------------------------------------------------
    # MessengerAdapter interface
    # ------------------------------------------------------------------

    async def send_approval(self, request: ApprovalRequest, choices: list[ApprovalChoice]) -> str:
        """Send an approval message with inline keyboard buttons.

        Returns the Telegram message_id as a string for later editing.
        """
        keyboard = [
            [
                InlineKeyboardButton(
                    choice.label,
                    callback_data={"request_id": request.request_id, "action": choice.action},
                )
                for choice in choices
            ]
        ]
        markup = InlineKeyboardMarkup(keyboard)

        text = f"Permission Request\n\nAction: {request.signature}"

        msg = await self._app.bot.send_message(
            chat_id=self._config.chat_id,
            text=text,
            reply_markup=markup,
        )
        return str(msg.message_id)

    async def update_approval(self, message_id: str, status: str, detail: str) -> None:
        """Edit the approval message to reflect a decision or expiry.

        Best-effort: logs a warning on failure, never raises.
        """
        try:
            text = f"{status}\n\n{detail}"
            await self._app.bot.edit_message_text(
                chat_id=self._config.chat_id,
                message_id=int(message_id),
                text=text,
            )
        except Exception:
            logger.warning("Failed to edit Telegram message %s", message_id, exc_info=True)

    async def on_approval_callback(
        self, callback: Callable[[ApprovalResult], Awaitable[None]]
    ) -> None:
        """Register the callback invoked when a guardian taps Allow / Deny."""
        self._callback = callback

    async def start(self) -> None:
        """Start listening — actual PTB lifecycle is managed by __main__.py."""

    async def stop(self) -> None:
        """Cancel all pending timeout tasks and clean up."""
        for task in self._pending.values():
            task.cancel()
        self._pending.clear()

    # ------------------------------------------------------------------
    # Timeout scheduling
    # ------------------------------------------------------------------

    def schedule_timeout(self, request_id: str, timeout: int, message_id: str) -> None:
        """Schedule an asyncio task that auto-denies after *timeout* seconds."""
        task = asyncio.create_task(self._timeout_handler(request_id, timeout, message_id))
        self._pending[request_id] = task

    async def _timeout_handler(self, request_id: str, timeout: int, message_id: str) -> None:
        """Fire after *timeout* seconds — resolve as deny if still pending."""
        await asyncio.sleep(timeout)

        async with self._resolve_lock:
            if request_id in self._resolved:
                return  # Already resolved by a user callback
            self._resolved.add(request_id)
            self._pending.pop(request_id, None)

        # Best-effort edit (may fail if message was already edited, network, etc.)
        await self.update_approval(message_id, "Expired", "Approval timed out")

        if self._callback:
            result = ApprovalResult(
                request_id=request_id,
                action="deny",
                user_id="timeout",
                timestamp=time.time(),
            )
            await self._callback(result)

    # ------------------------------------------------------------------
    # PTB callback query handlers
    # ------------------------------------------------------------------

    async def _handle_callback(self, update: Update, context: object) -> None:
        """Handle a valid inline-button press from a guardian."""
        query = update.callback_query

        # Major 4: Guard against non-dict data (defense-in-depth)
        if not isinstance(query.data, dict):
            await query.answer("Invalid callback data")
            return

        # FR5-AC2: only allowed users
        if query.from_user.id not in self._config.allowed_users:
            return  # silently ignore

        data = query.data  # dict: {"request_id": ..., "action": ...}
        request_id = data["request_id"]
        action = data["action"]

        async with self._resolve_lock:
            if request_id in self._resolved:
                await query.answer("Already resolved")
                return
            self._resolved.add(request_id)
            # Cancel the timeout task
            timeout_task = self._pending.pop(request_id, None)
            if timeout_task:
                timeout_task.cancel()

        await query.answer()

        user = query.from_user
        username = f"@{user.username}" if user.username else str(user.id)
        time_str = time.strftime("%H:%M")

        status = "Approved" if action == "allow" else "Denied"
        detail = f"{status} by {username} at {time_str}"

        await self.update_approval(str(query.message.message_id), status, detail)

        if self._callback:
            result = ApprovalResult(
                request_id=request_id,
                action=action,
                user_id=str(user.id),
                timestamp=time.time(),
            )
            await self._callback(result)

    async def _handle_invalid_callback(self, update: Update, context: object) -> None:
        """Handle stale callback data from buttons that survived a restart."""
        await update.callback_query.answer("This button has expired")
