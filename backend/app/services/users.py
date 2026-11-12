from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.models.base import utcnow


@dataclass(frozen=True, slots=True)
class TelegramSender:
    telegram_id: int
    chat_id: int
    username: str | None = None
    first_name: str | None = None


async def upsert_user(session: AsyncSession, sender: TelegramSender) -> User:
    """Create the user on first contact, keep username/first name fresh afterwards."""
    stmt = (
        pg_insert(User)
        .values(
            telegram_id=sender.telegram_id,
            chat_id=sender.chat_id,
            username=sender.username,
            first_name=sender.first_name,
        )
        .on_conflict_do_update(
            index_elements=[User.telegram_id],
            set_={
                "chat_id": sender.chat_id,
                "username": sender.username,
                "first_name": sender.first_name,
                "updated_at": utcnow(),
            },
        )
        .returning(User)
    )
    result = await session.execute(stmt, execution_options={"populate_existing": True})
    return result.scalar_one()
