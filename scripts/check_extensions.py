import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import engine  # noqa: E402


async def main() -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT name, default_version, installed_version "
                "FROM pg_available_extensions "
                "WHERE name IN ('pg_trgm', 'pgvector') ORDER BY name"
            )
        )
        rows = result.all()
        for name, default_version, installed_version in rows:
            print(f"{name}: available={default_version} installed={installed_version or 'no'}")
        if not rows:
            print("no optional search extensions reported by PostgreSQL")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
