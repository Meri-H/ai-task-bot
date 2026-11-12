from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.models import Base


async def test_models_and_migrations_are_in_sync(engine):
    """Fails if someone changes a model without writing the matching Alembic migration."""
    async with engine.connect() as conn:
        diffs = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        )
    assert diffs == []
