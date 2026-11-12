"""Telegram bot entrypoint (long polling).  Run with:  python -m app.bot.main"""

import asyncio
import logging
import sys

from aiogram import Dispatcher
from aiogram.types import BotCommand

from app.bot.handlers import router
from app.bot.intake import TaskIntake
from app.bot.middleware import AllowListMiddleware
from app.core.config import get_settings
from app.core.db import create_engine, create_session_factory
from app.core.logging import configure_logging
from app.core.queue import create_queue_pool
from app.telegram import build_bot

logger = logging.getLogger(__name__)


async def run() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather and put it in .env")
        return 2

    engine = create_engine(settings.database_url)
    pool = await create_queue_pool(settings.redis_url)
    bot = build_bot(settings)

    dp = Dispatcher()
    dp.message.outer_middleware(AllowListMiddleware(settings.allowed_user_ids))
    dp.include_router(router)

    try:
        me = await bot.get_me()
        logger.info("bot started as @%s", me.username)
        await bot.set_my_commands(
            [
                BotCommand(command="tasks", description="Your open tasks"),
                BotCommand(command="help", description="How to use the bot"),
            ]
        )
        await dp.start_polling(
            bot,
            allowed_updates=["message"],
            intake=TaskIntake(create_session_factory(engine), pool),
            settings=settings,
        )
    finally:
        await bot.session.close()
        await pool.aclose()
        await engine.dispose()
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
