"""Telegram client construction shared by the bot and the worker."""

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from app.core.config import Settings

DEFAULT_API_BASE = "https://api.telegram.org"


def build_bot(settings: Settings) -> Bot:
    """Create an aiogram Bot. `TELEGRAM_API_BASE` lets tests (or a self-hosted Bot API server)
    stand in for api.telegram.org."""
    session = None
    if settings.telegram_api_base.rstrip("/") != DEFAULT_API_BASE:
        session = AiohttpSession(api=TelegramAPIServer.from_base(settings.telegram_api_base.rstrip("/")))
    return Bot(token=settings.telegram_bot_token, session=session)
