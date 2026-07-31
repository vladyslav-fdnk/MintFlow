import logging

import psycopg

logger = logging.getLogger("mintflow.readiness")


async def is_postgresql_ready(database_url: str) -> bool:
    """Return whether PostgreSQL accepts a minimal query."""

    try:
        connection = await psycopg.AsyncConnection.connect(database_url, connect_timeout=2)
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
        return True
    except (psycopg.Error, OSError, TimeoutError):
        logger.warning("PostgreSQL readiness check failed", exc_info=True)
        return False
