"""
Database Session Management.
Supports async SQLite for zero-config local testing and PostgreSQL for production.
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import settings
from backend.app.db.models import Base

import os

logger = logging.getLogger(__name__)

# Normalize SQLite async URL.
# On Vercel / AWS Lambda serverless functions, the root filesystem is read-only, so SQLite runs in /tmp
is_serverless = bool(
    os.getenv("VERCEL")
    or os.getenv("VERCEL_ENV")
    or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
    or os.getenv("LAMBDA_TASK_ROOT")
)

db_url = settings.DATABASE_URL
if is_serverless and "sqlite" in db_url and "/tmp" not in db_url:
    db_url = "sqlite+aiosqlite:////tmp/academic_chatbot.db"
elif db_url.startswith("sqlite:///"):
    db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///")

try:
    engine = create_async_engine(db_url, echo=False)
except Exception as e:
    logger.warning(f"Could not initialize engine with {db_url}: {e}. Falling back to /tmp sqlite.")
    engine = create_async_engine("sqlite+aiosqlite:////tmp/academic_chatbot.db", echo=False)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _upgrade_schema(sync_conn) -> None:
    """
    Brings an existing database up to the current models without a migration tool.

    Deliberately narrow, so a restart can never quietly destroy data:

    * a column the models added and the table lacks is appended with ALTER TABLE ADD COLUMN,
      which keeps every existing row (the audit trail in particular);
    * a table whose definition changed in a way ALTER cannot express is recreated ONLY when it
      holds no rows;
    * anything else is left alone and logged, so a real migration can be planned.
    """
    inspector = inspect(sync_conn)
    existing = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue

        present = {column["name"]: column for column in inspector.get_columns(table.name)}
        missing = [column for column in table.columns if column.name not in present]

        addable, blocking = [], []
        for column in missing:
            if column.nullable or column.default is not None or column.server_default is not None:
                addable.append(column)
            else:
                blocking.append(column.name)

        # A column the model now allows to be empty, but the table still requires
        relaxed = [
            column.name for column in table.columns
            if column.nullable and column.name in present and not present[column.name]["nullable"]
        ]

        if blocking or relaxed:
            rows = sync_conn.execute(text(f'SELECT COUNT(*) FROM "{table.name}"')).scalar_one()
            if rows == 0:
                logger.info(f"Rebuilding empty table {table.name} to match the current model.")
                table.drop(sync_conn, checkfirst=True)
                table.create(sync_conn)
                continue
            logger.warning(
                f"Table {table.name} holds {rows} row(s) and differs from the model "
                f"(needs: {blocking + relaxed}). Left unchanged - migrate it deliberately."
            )

        for column in addable:
            column_type = column.type.compile(sync_conn.dialect)
            logger.info(f"Adding column {table.name}.{column.name} ({column_type}).")
            sync_conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))


async def init_db():
    """Creates any missing tables, then reconciles existing ones with the current models."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            try:
                await conn.run_sync(_upgrade_schema)
            except Exception as schema_err:
                logger.warning(f"Database schema upgrade non-fatal warning: {schema_err}")
    except Exception as e:
        logger.warning(f"Database init_db non-fatal warning: {e}")


async def get_db():
    """FastAPI dependency for database sessions."""
    async with AsyncSessionLocal() as session:
        yield session
