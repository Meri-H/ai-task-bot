import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)


class AllowListMiddleware(BaseMiddleware):
    """Optionally restrict the bot to a set of Telegram user ids (TELEGRAM_ALLOWED_USER_IDS)."""

    def __init__(self, allowed_user_ids: set[int]) -> None:
        self._allowed = allowed_user_ids

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,  # type: ignore[override]
        data: dict[str, Any],
    ) -> Any:
        if not self._allowed:
            return await handler(event, data)
        user = event.from_user
        if user is None or user.id not in self._allowed:
            logger.info("ignoring message from non-allowed user %s", user.id if user else None)
            await event.answer("Sorry, this bot is private.")
            return None
        return await handler(event, data)
