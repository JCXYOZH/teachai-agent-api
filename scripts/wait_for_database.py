"""Wait until PostgreSQL accepts connections before starting the API container."""

import asyncio
import os
import sys
from typing import Optional

import asyncpg

ASYNC_PREFIX = "postgresql+asyncpg://"


def to_dsn(database_url: str) -> str:
    """Strip the SQLAlchemy driver suffix so asyncpg can consume the URL."""
    if database_url.startswith(ASYNC_PREFIX):
        return "postgresql://" + database_url[len(ASYNC_PREFIX):]
    return database_url


async def wait_for_database(dsn: str, timeout: float, interval: float) -> bool:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    attempt = 0
    last_error: Optional[str] = None
    while True:
        attempt += 1
        try:
            connection = await asyncpg.connect(dsn, timeout=5)
        except Exception as exc:  # any failure means "not ready yet"
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            await connection.close()
            print(f"database ready after {attempt} attempt(s)")
            return True
        if loop.time() >= deadline:
            print(
                f"database not ready after {timeout:.0f}s ({attempt} attempts): {last_error}",
                file=sys.stderr,
            )
            return False
        await asyncio.sleep(interval)


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    timeout = float(os.environ.get("DB_WAIT_TIMEOUT_SECONDS", "90"))
    interval = float(os.environ.get("DB_WAIT_INTERVAL_SECONDS", "2"))
    ready = asyncio.run(wait_for_database(to_dsn(database_url), timeout, interval))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())